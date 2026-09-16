// The departures popup: one stop's scheduled departures with live delays,
// anchored to that stop's coordinates for as long as it is open.

import { esc, getJson, latencyBadgeHtml, timeHM, toDisplayedMinute,
         displayedShiftMin, minutesUntil } from './utils.js';
import { onScreen, placeBox } from './geo.js';
import { state, els } from './state.js';
import { stopsVisible } from './layers.js';

// Departures popup keeps itself alive while open, on two cadences. The tick
// re-renders from data already in hand, so the countdown keeps counting even
// when the backend is unreachable; the poll re-fetches, and is the slower of
// the two because each one costs a Pinot query (DELAYS_SQL).
const STOP_TICK_MS = 20000;
const STOP_POLL_MS = 60000;

// Departures popup. Fetched on click, then kept alive while open: a
// countdown that never ticks is worse than none, because it still reads as
// live data — "2 min" stayed "2 min" a minute later and a departed service
// stayed at the top of the list. The tick recomputes locally, the poll
// refreshes delays from the server (see STOP_TICK_MS / STOP_POLL_MS).
export async function showStopBox(info) {
  const { stopId, name, routes, lon, lat } = info.object;
  hideStopBox(); // stop the timers of a box already open on another stop
  state.stopBox = { stopId, name, routes, lon, lat,
                    data: null, ms: null, stats: null, w: 0, h: 0, tick: null, poll: null };
  els.stopBox.classList.remove('hidden');
  els.stopBox.innerHTML = `<h3>${esc(name)}</h3>Loading…`;
  positionStopBox(true);
  const box = state.stopBox;
  await pollStopBox();
  if (state.stopBox !== box) return; // closed or replaced during the fetch
  box.tick = setInterval(renderStopBox, STOP_TICK_MS);
  box.poll = setInterval(pollStopBox, STOP_POLL_MS);
}

async function pollStopBox() {
  const box = state.stopBox;
  if (!box) return;
  try {
    // The active line rides along: the server answers why this pole is
    // marked as served when none of the next 60 minutes belongs to it.
    const route = els.routeSelect.value;
    const { data, ms, stats } = await getJson(
      `/api/departures?stopId=${encodeURIComponent(box.stopId)}`
      + (route ? `&route=${encodeURIComponent(route)}` : ''));
    if (state.stopBox !== box) return; // another stop clicked mid-flight
    box.data = data;
    box.ms = ms;
    box.stats = stats;
    renderStopBox();
  } catch (err) {
    // A failed *re*-poll keeps the departures already on screen: the
    // countdown is still meaningful from the timestamps in hand, only the
    // delays are ageing. Only the first fetch has nothing to fall back to.
    if (state.stopBox === box && !box.data) {
      els.stopBox.innerHTML =
        `<h3>${esc(box.name)}</h3>Failed to load departures: ${esc(err.message)}`;
    }
  }
}

// "Sat " in front of anything that is not today — a 04:08 with no day on it
// reads as four in the morning that has already been and gone.
function dayPrefix(ts) {
  const t = new Date(ts);
  return t.toDateString() === new Date().toDateString() ? ''
    : t.toLocaleDateString([], { weekday: 'short' }) + ' ';
}

// Expected time leads, schedule is the footnote: a rider wants to know when
// the tram is actually there, and heading the row with 11:55 for a service
// that will not arrive until 11:59 buries the delay in a chip. Chip and
// struck-through schedule are one decision (displayedShiftMin, unit-tested in
// utils.js): both appear together or neither does, and the chip is the gap
// between the two times the row is showing. Deriving them separately is what
// let a slightly-early tram print "22:17 22:18" with no chip to explain it. A
// departure with no live vehicle matched (delayMin null) is schedule-only:
// one value, nothing struck out.
function departureRowHtml(d, now) {
  const eta = d.estimated ?? d.time;
  const shown = toDisplayedMinute(eta);
  const shift = d.delayMin == null ? 0 : displayedShiftMin(d.time, eta);
  const sched = shift
    ? ` <span class="sched">${timeHM(toDisplayedMinute(d.time))}</span>` : '';
  const lateness = shift > 0 ? 'late' : 'early';
  const sign = shift > 0 ? '+' : '';
  const delay = shift
    ? ` <span class="delay ${lateness}">${sign}${shift}</span>` : '';
  return `<tr><td>${dayPrefix(shown)}${timeHM(shown)}${delay}${sched}</td>
              <td><span class="route-badge">${esc(d.route)}</span></td>
              <td class="headsign">${esc(d.headsign)}</td>
              <td>${minutesUntil(shown, now)} min</td></tr>`;
}

// What the list on screen actually is. "No further" vs "none scheduled" — the
// local filter can empty a list the server sent full, and those are different
// facts to a waiting rider.
function modeLabel(box, live) {
  if (live.length) {
    return box.data.mode === 'hour'
      ? 'Next 60 minutes' : 'No departures within an hour — next scheduled';
  }
  if (!box.routes.length) return 'Stop not in use — no scheduled departures';
  return box.data.departures.length
    ? 'No further departures' : 'No scheduled departures';
}

// Present only when a line is filtered and none of the rows above is that
// line — a pole served four times a day looks identical on the filtered map
// to one served every six minutes, so the popup says which it is.
function routeNextHtml(box) {
  if (!('routeNext' in box.data)) return '';
  const route = esc(els.routeSelect.value);
  const next = box.data.routeNext;
  if (!next) {
    return `<div class="route-next">No further line
             <span class="route-badge">${route}</span>
             departures today or tomorrow</div>`;
  }
  const shown = toDisplayedMinute(next.estimated);
  return `<div class="route-next">Line <span class="route-badge">${route}</span>
             next departs ${esc(dayPrefix(shown))}${timeHM(shown)}</div>`;
}

function renderStopBox() {
  const box = state.stopBox;
  if (!box?.data) return;
  const now = Date.now();
  // The server filters departed services against its own clock at fetch time,
  // so between polls this is the only thing keeping a run that has already
  // left off the top of the list.
  const live = box.data.departures.filter(d => (d.estimated ?? d.time) >= now);
  els.stopBox.innerHTML = `
    <button class="close" aria-label="Close">✕</button>
    <h3>${esc(box.name)}</h3>
    <div class="mode">${modeLabel(box, live)}${latencyBadgeHtml(box.ms, box.stats)}</div>
    <table>${live.map(d => departureRowHtml(d, now)).join('')}</table>
    ${routeNextHtml(box)}`;
  els.stopBox.querySelector('.close').onclick = hideStopBox;
  positionStopBox(true); // re-measure: the row count just changed
}

// Anchored to the stop's *coordinates*, not to the pixel that was clicked:
// re-projected on every camera frame, the popup rides along with its pole
// through pan and zoom instead of hanging over whatever the map slid under
// it. Size is measured rather than assumed — the old fixed 300x200 guess put
// most of a busy stop's thirty rows below the map edge — but only when the
// content changed (`measure`), since a camera frame cannot resize the box
// and reading offsetWidth after writing left/top forces a reflow each time.
export function positionStopBox(measure) {
  const box = state.stopBox;
  if (!box) return;
  const { mapView, mapContainer } = els;
  const size = { width: mapContainer.clientWidth, height: mapContainer.clientHeight };
  const p = state.map.project([box.lon, box.lat]);
  // Nothing left to point at: the poles are hidden, or this one has been
  // panned off the map.
  if (!stopsVisible() || !onScreen(p, size)) {
    hideStopBox();
    return;
  }
  if (measure || !box.w) {
    box.w = els.stopBox.offsetWidth;
    box.h = els.stopBox.offsetHeight;
  }
  // project() is relative to the map canvas, which is inset inside #map-view
  // (the popup's offset parent) — add that gutter back.
  const { left, top } = placeBox(
    { x: p.x + mapContainer.offsetLeft, y: p.y + mapContainer.offsetTop },
    { width: box.w, height: box.h },
    { width: mapView.clientWidth, height: mapView.clientHeight });
  els.stopBox.style.left = left + 'px';
  els.stopBox.style.top = top + 'px';
}

export function hideStopBox() {
  if (state.stopBox) {
    clearInterval(state.stopBox.tick);
    clearInterval(state.stopBox.poll);
    state.stopBox = null;
  }
  els.stopBox.classList.add('hidden');
}
