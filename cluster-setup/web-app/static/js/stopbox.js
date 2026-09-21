// The departures popup: one stop's scheduled departures with live delays,
// anchored to that stop's coordinates for as long as it is open.

import { esc, getJson, latencyBadgeHtml, timeHM, toDisplayedMinute,
         displayedShiftMin, minutesUntil } from './utils.js';
import { onScreen, placeBox } from './geo.js';
import { state, els } from './state.js';
import { stopsVisible } from './layers.js';

// Two cadences while open: the poll re-fetches delays every 10 s (matching
// REFRESH_MS in map.js — both read the same upsert table, and a slower poll
// let a tracked vehicle's badge and this popup disagree on its delay); the
// tick re-renders from data in hand so the countdown keeps counting and
// departed rows keep dropping even when a poll fails.
const STOP_TICK_MS = 20000;
const STOP_POLL_MS = 10000;

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

// Expected time leads, schedule is the footnote. Chip and struck-through
// schedule are one decision (displayedShiftMin in utils.js): both appear or
// neither does. No matched vehicle (delayMin null) means schedule-only.
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

// Anchored to the stop's coordinates and re-projected on every camera frame,
// so the popup rides along with its pole. Its size is measured, but only
// when the content changed (`measure`): a camera frame cannot resize the box,
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
