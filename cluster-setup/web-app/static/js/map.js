import { colorForRoute, time24, timeHM, esc, isTram, isNightBus, latencyMs, latencyBadgeHtml } from './utils.js';
import { OFF_ROUTE_M, projectOnPath, nearestRouteStop, needsRecentre, placeBox,
         thinOverlapping } from './geo.js';

const REFRESH_MS = 10000;
// Departures popup keeps itself alive while open, on two cadences. The tick
// re-renders from data already in hand, so the countdown keeps counting even
// when the backend is unreachable; the poll re-fetches, and is the slower of
// the two because each one costs a Pinot query (DELAYS_SQL).
const STOP_TICK_MS = 20000;
const STOP_POLL_MS = 60000;
const INITIAL_VIEW = { center: [18.6466, 54.352], zoom: 15 }; // Gdansk Old Town
const HALO_PERIOD_MS = 2200;

// Single app-state object rather than many top-level lets, so refresh/render
// interactions are easier to follow and functions can't accidentally read a
// stale closure variable. `progress` is added onto the selectedTrip object
// itself (see tripPathLayers) since it's per-selection, recomputed each render.
const state = {
  map: null,
  overlay: null,
  selectedTrip: null, // when set, has fields vehicleId, routeId, tripId, path, progress
  followSelected: false, // camera tracks selectedTrip until the user moves the map
  stopBox: null, // when open: { stopId, name, x, y, routes, data, ms, tick, poll }
  lastRows: [],
  lastUpdated: 0,
  positionsMs: null, // Pinot's timeUsedMs for the last /api/positions fetch
  heatmapMs: null,   // same, for the last /api/heatmap fetch
  stopsData: [],
  heatmapData: [],
};

function vehicleActionText(d) {
  if (d.inService === false) return 'Not currently in service';
  const verb = state.selectedTrip?.vehicleId === d.vehicleId ? 'hide' : 'show';
  return `Click to ${verb} the route path`;
}

function tooltip({ object: d }) {
  if (!d) return null;
  if (d.stopId) {
    if (!d.routes.length) {
      return {
        html: `<b>${esc(d.name)}</b><br>
               Stop not in use — no scheduled departures`,
      };
    }
    return {
      html: `<b>${esc(d.name)}</b><br>
             Lines: ${esc(d.routes.join(', '))}<br>
             Click for departures`,
    };
  }
  return {
    html: `<b>Route:</b> ${d.route}<br>
           <b>Headsign:</b> ${d.headsign || '—'}<br>
           <b>Delay:</b> ${Math.round(d.delay / 60)} min<br>
           <b>Speed:</b> ${d.speed} km/h<br>
           <b>Seen:</b> ${time24(d.lastSeen)}<br>
           ${vehicleActionText(d)}`,
  };
}

// Marker layout, everything anchored on the vehicle position (route text
// centred at 0):  [ ↑ route ]
// The arrow sits inside the box's left backgroundPadding; its offset tracks
// half the route text width (~8 px per glyph at size 14 bold).
function routeTextWidth(d) {
  return String(d.route || '?').length * 8;
}
function arrowOffset(d) {
  return [-(routeTextWidth(d) / 2 + 9), 0];
}

// Called when the map tab becomes visible again — its container was
// display:none while hidden, so Mapbox needs telling its size may have
// changed.
export function resizeMap() {
  state.map?.resize();
}

export async function initMap() {
  const cfg = await (await fetch('/api/config')).json();
  mapboxgl.accessToken = cfg.mapboxToken;

  // The map is created exactly once; refreshes below never touch it.
  const map = new mapboxgl.Map({
    container: 'map',
    // light-v11: near-monochrome basemap so the colored markers and the
    // grey/blue route path stand out (was streets-v9). It defaults to the
    // globe projection, which breaks deck.gl overlay alignment and picking —
    // force mercator.
    style: 'mapbox://styles/mapbox/light-v11',
    projection: 'mercator',
    ...INITIAL_VIEW,
  });
  // showCompass: false drops the "reset bearing to north" button. Nothing
  // in this app rotates the map, so it was a no-op control taking up room.
  map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), 'top-right');
  state.map = map;

  const overlay = new deck.MapboxOverlay({
    layers: [],
    getTooltip: tooltip,
    onClick: info => {
      if (info.layer?.id === 'stops' && info.object) {
        showStopBox(info);
        return;
      }
      hideStopBox();
      if (info.layer?.id === 'vehicles' && info.object) selectVehicle(info.object);
      else clearTripPath(); // empty-map click clears the drawn route
    },
  });
  map.addControl(overlay);
  state.overlay = overlay;

  // Debug hooks for automated testing — intentional seam, not incidental:
  // browser-driven verification (chromium-cli/Playwright) drives deck.gl
  // picking and inspects layer state through these.
  globalThis._map = map;
  globalThis._overlay = overlay;
  globalThis._log = [];
  map.on('moveend', () => globalThis._log.push(
    { t: Date.now(), ev: 'moveend', zoom: map.getZoom() }));

  const statusEl = document.getElementById('panel-status');
  const routeSelect = document.getElementById('route-filter');
  const stopBox = document.getElementById('stop-box');
  const heatmapToggle = document.getElementById('heatmap-toggle');
  const histogramEl = document.getElementById('route-histogram');

  // 24 h ping-density heatmap, queried live from Pinot on every toggle-on —
  // no caching anywhere, so the latency badge always shows a real query.
  async function loadHeatmap() {
    try {
      const resp = await fetch('/api/heatmap');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      state.heatmapData = await resp.json();
      state.heatmapMs = latencyMs(resp);
    } catch (err) {
      statusEl.textContent = `Heatmap failed: ${err.message}`;
    }
  }

  function heatmapLayers() {
    if (!heatmapToggle.checked || !state.heatmapData.length) return [];
    return [new deck.HeatmapLayer({
      id: 'heatmap',
      data: state.heatmapData,
      getPosition: d => [d.lonCell, d.latCell],
      getWeight: d => d.pings,
      radiusPixels: 40,
      opacity: 0.55,
    })];
  }

  heatmapToggle.onchange = async () => {
    if (heatmapToggle.checked) {
      await loadHeatmap();
      clearTripPath(); // heatmap and live vehicles are separate modes
    }
    render();
  };

  // Interactive drill-down: selecting a route queries its whole-history,
  // per-hour average delay in one shot (see app/pinot.py route_hourly_delay —
  // deliberately uncached, so the badge shows Pinot's real query time, not a
  // cache hit) and renders it as a small bar chart. Not fetched on every 10 s
  // refresh: the underlying data barely moves within a session, only the
  // selection does.
  async function loadRouteHistogram(route) {
    if (!route) {
      histogramEl.classList.add('hidden');
      histogramEl.innerHTML = '';
      return;
    }
    try {
      const resp = await fetch(`/api/route-delay-histogram?route=${encodeURIComponent(route)}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const rows = await resp.json();
      renderRouteHistogram(route, rows, latencyMs(resp));
    } catch (err) {
      histogramEl.classList.remove('hidden');
      histogramEl.innerHTML = `<h4>Route ${esc(route)} — delay by hour</h4>`
        + `Failed to load: ${esc(err.message)}`;
    }
  }

  function renderRouteHistogram(route, rows, ms) {
    const byHour = new Map(rows.map(r => [r.hour, r]));
    // Always draw all 24 slots so a route with sparse-hour coverage (e.g. a
    // night line) still reads as a full day, not a squeezed partial chart.
    const hours = Array.from({ length: 24 }, (_, h) => {
      const key = `${String(h).padStart(2, '0')}:00`;
      return byHour.get(key) || null;
    });
    const values = hours.map(r => r ? r.avgDelaySec : null).filter(v => v != null);
    const min = values.length ? Math.min(0, ...values) : 0;
    const max = values.length ? Math.max(0, ...values) : 1;
    const range = (max - min) || 1;
    const bars = hours.map((r, h) => {
      if (!r) return `<div class="bar" style="height:0" title="${h}:00 — no data"></div>`;
      const pct = Math.round(((r.avgDelaySec - min) / range) * 100);
      return `<div class="bar" style="height:${Math.max(pct, 2)}%" ` +
             `title="${h}:00 — avg ${r.avgDelaySec}s over ${r.snapshots} snapshots"></div>`;
    }).join('');
    const ticks = [0, 6, 12, 18].map(h => `<span>${h}:00</span>`).join('');
    histogramEl.classList.remove('hidden');
    histogramEl.innerHTML =
      `<h4>Route ${esc(route)} — avg delay by hour (whole history)` +
      `${latencyBadgeHtml(ms)}</h4>` +
      `<div class="bars">${bars}</div>` +
      `<div class="hours">${ticks}</div>`;
  }

  // Clicking a vehicle draws the trajectory of the trip it is serving, split
  // at the vehicle: covered part grey, part ahead light blue. Clicking the
  // same vehicle again, another vehicle, or empty map clears/replaces it.
  async function selectVehicle(d) {
    if (state.selectedTrip?.vehicleId === d.vehicleId) {
      clearTripPath();
      return;
    }
    if (d.inService === false) {
      // No active trip (e.g. laying over between runs) — nothing to draw.
      statusEl.textContent =
        `Vehicle on line ${d.route} is not currently in service`;
      return;
    }
    state.selectedTrip = { vehicleId: d.vehicleId, routeId: d.routeId,
                           tripId: d.tripId, path: null };
    state.followSelected = true;
    centerOnVehicle(d, { force: true });
    await loadTripPath(d);
  }

  async function loadTripPath(d) {
    try {
      const resp = await fetch(
        `/api/route-shape?routeId=${d.routeId}&tripId=${d.tripId}`);
      if (resp.status === 404) { // between trips / not in today's plan
        statusEl.textContent =
          `No route path for line ${d.route} (course ${d.tripId})`;
        clearTripPath();
        return;
      }
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const { path } = await resp.json();
      // clicks can race the fetch; apply only if this vehicle is still selected
      if (state.selectedTrip?.vehicleId === d.vehicleId) {
        state.selectedTrip.path = path;
        render();
      }
    } catch (err) {
      statusEl.textContent = `Route path failed: ${err.message}`;
      clearTripPath();
    }
  }

  function clearTripPath() {
    if (!state.selectedTrip) return;
    state.selectedTrip = null;
    state.followSelected = false;
    render();
  }

  // The split is recomputed from the fresh position on every render, so each
  // 10 s refresh advances the grey portion without re-fetching the geometry.
  // The projected point closes both halves, so the colour changes exactly at
  // the vehicle dot.
  function tripPathLayers(rows) {
    const trip = state.selectedTrip;
    if (!trip?.path || trip.path.length < 2) return [];
    const v = rows.find(d => d.vehicleId === trip.vehicleId);
    if (!v) return [];
    let proj = projectOnPath(trip.path, v, trip.progress);
    const onRoute = proj.meters <= OFF_ROUTE_M;
    if (onRoute) {
      trip.progress = proj.progress;
    } else {
      // Off-route (e.g. standing at a depot): nothing is covered yet — anchor
      // the line at this route's stop nearest to the vehicle, where the trip
      // will actually start, instead of at a raw nearest shape point.
      trip.progress = null;
      const stop = nearestRouteStop(state.stopsData, v);
      if (stop) {
        proj = projectOnPath(trip.path, { lon: stop.lon, lat: stop.lat, speed: 0 }, null);
      }
    }
    const style = {
      getPath: p => p,
      getWidth: 5,
      widthMinPixels: 3,
      widthMaxPixels: 8,
      capRounded: true,
      jointRounded: true,
    };
    const layers = [];
    if (onRoute) {
      layers.push(new deck.PathLayer({
        id: 'trip-covered',
        data: [[...trip.path.slice(0, proj.i + 1), proj.point]],
        getColor: [128, 128, 128, 180],
        ...style,
      }));
    }
    layers.push(new deck.PathLayer({
      id: 'trip-ahead',
      data: [[proj.point, ...trip.path.slice(proj.i + 1)]],
      getColor: [110, 180, 255, 220],
      ...style,
    }));
    return layers;
  }

  // A pulsing ring under the selected vehicle's badge, so it stays
  // identifiable a few minutes after picking it out of a cluster of nearby
  // vehicles. Pixel-sized (not geo-sized) so it reads the same at any zoom.
  // Driven by a dedicated fast interval (below), separate from the 10 s data
  // refresh, so the pulse is smooth without touching the "only the dot layer
  // redraws on refresh" perf design.
  function selectedVehicleHaloLayer(rows) {
    if (!state.selectedTrip) return [];
    const v = rows.find(d => d.vehicleId === state.selectedTrip.vehicleId);
    if (!v) return [];
    // 0..1..0 triangle wave: radius and opacity swell and shrink together.
    const phase = (Date.now() % HALO_PERIOD_MS) / HALO_PERIOD_MS;
    const wave = phase < 0.5 ? phase * 2 : 2 - phase * 2;
    return [new deck.ScatterplotLayer({
      id: 'selected-vehicle-halo',
      data: [v],
      getPosition: d => [d.lon, d.lat],
      getRadius: 16 + wave * 10,
      radiusUnits: 'pixels',
      stroked: true,
      filled: false,
      getLineColor: [230, 0, 230, 140 + wave * 115],
      lineWidthMinPixels: 4,
      pickable: false,
      updateTriggers: { getRadius: phase, getLineColor: phase },
    })];
  }

  // Stops are static for the day; fetched once. The server answers 503 for
  // the first seconds after start while it parses the GTFS feed — retry.
  async function loadStops() {
    try {
      const resp = await fetch('/api/stops');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      state.stopsData = await resp.json();
      render();
    } catch {
      setTimeout(loadStops, 10000);
    }
  }

  // Departures popup. Fetched on click, then kept alive while open: a
  // countdown that never ticks is worse than none, because it still reads as
  // live data — "2 min" stayed "2 min" a minute later and a departed service
  // stayed at the top of the list. The tick recomputes locally, the poll
  // refreshes delays from the server (see STOP_TICK_MS / STOP_POLL_MS).
  async function showStopBox(info) {
    const { stopId, name, routes } = info.object;
    hideStopBox(); // stop the timers of a box already open on another stop
    state.stopBox = { stopId, name, routes, x: info.x, y: info.y,
                      data: null, ms: null, tick: null, poll: null };
    stopBox.classList.remove('hidden');
    stopBox.innerHTML = `<h3>${esc(name)}</h3>Loading…`;
    positionStopBox();
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
      const route = routeSelect.value;
      const resp = await fetch(
        `/api/departures?stopId=${encodeURIComponent(box.stopId)}`
        + (route ? `&route=${encodeURIComponent(route)}` : ''));
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const dep = await resp.json();
      if (state.stopBox !== box) return; // another stop clicked mid-flight
      box.data = dep;
      box.ms = latencyMs(resp);
      renderStopBox();
    } catch (err) {
      // A failed *re*-poll keeps the departures already on screen: the
      // countdown is still meaningful from the timestamps in hand, only the
      // delays are ageing. Only the first fetch has nothing to fall back to.
      if (state.stopBox === box && !box.data) {
        stopBox.innerHTML =
          `<h3>${esc(box.name)}</h3>Failed to load departures: ${esc(err.message)}`;
      }
    }
  }

  function renderStopBox() {
    const box = state.stopBox;
    if (!box?.data) return;
    const now = Date.now();
    const today = new Date(now).toDateString();
    // "Sat " in front of anything that is not today — a 04:08 with no day on
    // it reads as four in the morning that has already been and gone.
    const dayPrefix = ts => {
      const t = new Date(ts);
      return t.toDateString() === today ? ''
        : t.toLocaleDateString([], { weekday: 'short' }) + ' ';
    };
    // The server filters departed services against its own clock at fetch
    // time, so between polls this is the only thing keeping a run that has
    // already left off the top of the list.
    const live = box.data.departures.filter(d => (d.estimated ?? d.time) >= now);
    const rows = live.map(d => {
      const eta = d.estimated ?? d.time;
      const day = dayPrefix(eta);
      const mins = Math.max(0, Math.round((eta - now) / 60000));
      // Expected time leads, schedule is the footnote: a rider wants to know
      // when the tram is actually there, and heading the row with 11:55 for a
      // service that will not arrive until 11:59 buries the delay in a chip.
      // The struck-through scheduled time appears only when the delay is big
      // enough to move the displayed minute, so an on-time run stays one value.
      const sched = (d.delayMin != null && timeHM(d.time) !== timeHM(eta))
        ? ` <span class="sched">${timeHM(d.time)}</span>` : '';
      let delay = '';
      if (d.delayMin) {
        delay = d.delayMin > 0
          ? ` <span class="delay late">+${d.delayMin}</span>`
          : ` <span class="delay early">${d.delayMin}</span>`;
      }
      return `<tr><td>${day}${timeHM(eta)}${delay}${sched}</td>
                  <td><span class="route-badge">${esc(d.route)}</span></td>
                  <td class="headsign">${esc(d.headsign)}</td>
                  <td>${mins} min</td></tr>`;
    }).join('');
    let label;
    if (!live.length) {
      // "no further" vs "none scheduled" — the local filter can empty a list
      // the server sent full, and those are different facts to a waiting rider.
      if (!box.routes.length) label = 'Stop not in use — no scheduled departures';
      else if (box.data.departures.length) label = 'No further departures';
      else label = 'No scheduled departures';
    } else if (box.data.mode === 'hour') {
      label = 'Next 60 minutes';
    } else {
      label = 'No departures within an hour — next scheduled';
    }
    // Present only when a line is filtered and none of the rows above is
    // that line — a pole served four times a day looks identical on the map
    // to one served every six minutes, so the popup says which it is.
    let note = '';
    if ('routeNext' in box.data) {
      const n = box.data.routeNext;
      note = n
        ? `<div class="route-next">Line <span class="route-badge">${esc(routeSelect.value)}</span>
             next departs ${esc(dayPrefix(n.estimated))}${timeHM(n.estimated)}</div>`
        : `<div class="route-next">No further line
             <span class="route-badge">${esc(routeSelect.value)}</span>
             departures today or tomorrow</div>`;
    }
    stopBox.innerHTML = `
      <button class="close" aria-label="Close">✕</button>
      <h3>${esc(box.name)}</h3>
      <div class="mode">${label}${latencyBadgeHtml(box.ms)}</div>
      <table>${rows}</table>${note}`;
    stopBox.querySelector('.close').onclick = hideStopBox;
    positionStopBox(); // re-measure: the row count just changed
  }

  // Anchored to the click, but measured rather than assumed. The old fixed
  // 300x200 guess put most of a busy stop's thirty rows below the map edge.
  function positionStopBox() {
    const box = state.stopBox;
    if (!box) return;
    const view = document.getElementById('map-view');
    const { left, top } = placeBox(
      { x: box.x, y: box.y },
      { width: stopBox.offsetWidth, height: stopBox.offsetHeight },
      { width: view.clientWidth, height: view.clientHeight });
    stopBox.style.left = left + 'px';
    stopBox.style.top = top + 'px';
  }

  function hideStopBox() {
    if (state.stopBox) {
      clearInterval(state.stopBox.tick);
      clearInterval(state.stopBox.poll);
      state.stopBox = null;
    }
    stopBox.classList.add('hidden');
  }

  // Route list is the full GTFS schedule, fetched once — not just whichever
  // routes happen to have a live vehicle this refresh — so night lines and
  // temporarily idle routes stay selectable instead of disappearing. The
  // server 503s until the GTFS feed finishes parsing; retry.
  async function loadRoutes() {
    try {
      const resp = await fetch('/api/routes');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      buildRouteOptions(await resp.json());
    } catch {
      setTimeout(loadRoutes, 10000);
    }
  }

  function buildRouteOptions(routes) {
    const sorted = [...routes]
      .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    routeSelect.innerHTML = '';
    routeSelect.append(new Option('All vehicles', ''));
    for (const [label, group] of [
      ['Trams', sorted.filter(isTram)],
      ['Buses', sorted.filter(r => !isTram(r) && !isNightBus(r))],
      ['Night buses', sorted.filter(isNightBus)],
    ]) {
      if (!group.length) continue;
      const g = document.createElement('optgroup');
      g.label = label;
      for (const r of group) g.append(new Option(r, r));
      routeSelect.append(g);
    }
    routeSelect.disabled = false;
    updateRouteActivity(state.lastRows);
  }

  // Routes options are built once (above) and never rebuilt, so this only
  // toggles a class per refresh — an open dropdown or the user's selection
  // is never disturbed. Routes with no vehicle in the latest poll (idle
  // right now, not gone from the schedule) render greyed out, still
  // selectable — see .route-inactive in styles.css.
  function updateRouteActivity(rows) {
    const active = new Set(rows.map(d => String(d.route || '?')));
    for (const opt of routeSelect.querySelectorAll('option[value]:not([value=""])')) {
      opt.classList.toggle('route-inactive', !active.has(opt.value));
    }
  }

  // Zoomed out to the whole city ~300 badges pile into unreadable mush, so
  // drop the ones that would overlap. Recomputed only when the data, camera or
  // selection actually changes: render() also runs every 100 ms to animate the
  // halo, and reprojecting every vehicle on each of those frames would be pure
  // waste. thinOverlapping is in geo.js, unit-tested there.
  let thinCache = { key: null, rows: null };
  function thinVehicles(rows) {
    const c = map.getCenter();
    const sel = state.selectedTrip?.vehicleId ?? '';
    const key = `${state.lastUpdated}|${rows.length}|${map.getZoom().toFixed(3)}`
              + `|${c.lng.toFixed(5)},${c.lat.toFixed(5)}|${sel}`;
    if (thinCache.key === key) return thinCache.rows;
    const pts = rows.map(d => {
      const p = map.project([d.lon, d.lat]);
      return { x: p.x, y: p.y, priority: d.vehicleId === sel ? 1 : 0 };
    });
    const keep = thinOverlapping(pts);
    const out = rows.filter((_, i) => keep.has(i));
    thinCache = { key, rows: out };
    return out;
  }

  function render() {
    const route = routeSelect.value;
    const rows = route
      ? state.lastRows.filter(d => String(d.route || '?') === route)
      : state.lastRows;
    // Heatmap (24 h aggregate) and live vehicles are alternate modes, not a
    // combined view — showing both at once buried the badges (and stop poles)
    // under the busiest hot spots, which are usually the same clusters.
    const heatOn = heatmapToggle.checked;
    // One decision, both vehicle layers: a badge and its arrow are one visual
    // unit and must appear or disappear together.
    const shown = heatOn ? rows : thinVehicles(rows);
    // Markers jump to the newly scraped position on each refresh — no
    // interpolated movement between API polls.
    // Only the vehicle layers are replaced; deck.gl diffs them on the GPU
    // and the base map keeps its tiles, camera and WebGL context.
    overlay.setProps({
      layers: [
        // Density heatmap — lowest layer, everything else reads on top of it.
        ...heatmapLayers(),
        // Selected trip's trajectory — drawn under stops and vehicles
        // (path layers are not pickable).
        ...(heatOn ? [] : tripPathLayers(rows)),
        // Stop poles — static, rendered under the vehicles so vehicles win
        // picking conflicts; hidden when zoomed out to avoid clutter, and
        // hidden entirely in heatmap mode (same reasoning as the vehicle
        // layers above — the poles bury the density colors they'd sit on).
        // With a line selected, only that line's stops show — at any zoom,
        // since fitting a long line can land below the threshold.
        new deck.ScatterplotLayer({
          id: 'stops',
          data: route
            ? state.stopsData.filter(s => s.routes.includes(route))
            : state.stopsData,
          visible: !heatOn && (route ? true : map.getZoom() >= 13),
          getPosition: d => [d.lon, d.lat],
          getRadius: 14,
          radiusMinPixels: 3,
          radiusMaxPixels: 8,
          // grey = not in use: no vehicle departs from this pole today
          getFillColor: d => d.routes.length
            ? [37, 99, 235, 150] : [156, 163, 175, 160],
          stroked: true,
          getLineColor: [255, 255, 255, 220],
          lineWidthMinPixels: 1,
          pickable: true,
        }),
        // Ring around the tracked vehicle — under its badge so the badge and
        // arrow stay on top and pickable.
        ...(heatOn ? [] : selectedVehicleHaloLayer(rows)),
        // Route-number box — the text background is the box itself, with
        // extra left padding reserving room for the heading arrow.
        new deck.TextLayer({
          id: 'vehicles',
          data: heatOn ? [] : shown,
          characterSet: 'auto',
          getText: d => String(d.route || '?'),
          getPosition: d => [d.lon, d.lat],
          getSize: 14,
          getColor: [255, 255, 255],
          fontFamily: 'system-ui, sans-serif',
          fontWeight: 700,
          background: true,
          getBackgroundColor: d => colorForRoute(d.route),
          backgroundPadding: [18, 3, 6, 3],
          getBorderColor: [255, 255, 255, 200],
          getBorderWidth: 1,
          pickable: true,
        }),
        // Heading arrow in the left slot of the box, pointing where the
        // vehicle is going. Hidden only for vehicles standing at a terminus
        // of their own course — the schedule-derived atTerminus flag from
        // /api/positions (within 150 m of the trip's first/last stop). An
        // ordinary speed-0 stop at lights or a stop keeps the last heading.
        new deck.TextLayer({
          id: 'vehicle-arrows',
          data: heatOn ? [] : shown.filter(d => d.speed > 0 || !d.atTerminus),
          characterSet: ['↑'],
          getText: () => '↑',
          getPosition: d => [d.lon, d.lat],
          getPixelOffset: arrowOffset,
          // deck.gl angles are counter-clockwise; compass bearings clockwise
          getAngle: d => -(d.direction || 0),
          getColor: [255, 255, 255],
          getSize: 17,
          fontFamily: 'system-ui, sans-serif',
          fontWeight: 700,
        }),
      ],
    });
    const count = route ? `${rows.length} of ${state.lastRows.length}` : `${rows.length}`;
    const ms = heatOn ? state.heatmapMs : state.positionsMs;
    statusEl.innerHTML = `${count} vehicles · updated ${time24(state.lastUpdated)}`
      + latencyBadgeHtml(ms);
  }

  // Centering on a tracked vehicle — both on the click that selects it and
  // on every refresh while it stays selected. panTo (not flyTo/fitBounds)
  // never touches zoom: clicking a badge to read its trip is not a request
  // to re-frame the map, and a zoomed-out view is often exactly the context
  // the user wants the trajectory drawn in. Contrast fitToSelection below,
  // which does set zoom — but only for an explicit dropdown pick.
  // `force` marks the camera moves the user actually asked for — the click
  // that selects a vehicle. Refresh ticks come through without it and go via
  // the deadzone, so a vehicle creeping across the middle of the screen costs
  // no camera movement at all; only one that drifts out of the middle 60%
  // pulls the map back. Before this, every poll panned, and a bus that moved
  // 8 m slid the whole map out from under whoever was reading its trajectory.
  function centerOnVehicle(d, { force = false } = {}) {
    if (!force) {
      const c = map.getContainer();
      // clientWidth/clientHeight, not the canvas's width/height: map.project
      // returns CSS pixels and the canvas is sized in device pixels.
      const size = { width: c.clientWidth, height: c.clientHeight };
      if (!needsRecentre(map.project([d.lon, d.lat]), size)) return;
    }
    map.panTo([d.lon, d.lat]);
  }

  // The user's own pan/zoom ends the follow for good (until the next
  // selection): moving the map is a deliberate "I want to look here", and
  // silently undoing it on the next refresh is worse than losing the
  // vehicle off-frame — panning ahead along the light-blue segment to see
  // where the trip goes is exactly what the drawn trajectory invites.
  // Mapbox marks user gestures with originalEvent; our own panTo has none,
  // so the follow never cancels itself.
  map.on('movestart', e => { if (e.originalEvent) state.followSelected = false; });

  // Camera fit for the moment a line is picked in the dropdown — called from
  // routeSelect.onchange only, never from refresh(): once the user has the
  // line in view they may pan/zoom freely, and a 10 s re-fit would keep
  // snapping the camera back. "All vehicles" flies home to the initial view.
  function fitToSelection() {
    const route = routeSelect.value;
    if (!route) {
      map.flyTo(INITIAL_VIEW);
      return;
    }
    const rows = state.lastRows.filter(d => String(d.route || '?') === route);
    const stops = state.stopsData.filter(s => s.routes.includes(route));
    if (!rows.length && !stops.length) return;
    const bounds = new mapboxgl.LngLatBounds();
    for (const d of rows) bounds.extend([d.lon, d.lat]);
    // Vehicles alone can cluster on one segment of the line, leaving the
    // route's other stops rendered but outside the fitted view — include
    // them in the bounds so the whole line's stops stay visible too.
    for (const s of stops) bounds.extend([s.lon, s.lat]);
    // maxZoom keeps a single-vehicle line from zooming into rooftop level.
    map.fitBounds(bounds, { padding: 80, maxZoom: 15 });
  }

  routeSelect.onchange = () => {
    // the drawn path belongs to one vehicle; drop it if the filter hides it
    if (state.selectedTrip && routeSelect.value) {
      const v = state.lastRows.find(d => d.vehicleId === state.selectedTrip.vehicleId);
      if (!v || String(v.route || '?') !== routeSelect.value) {
        state.selectedTrip = null;
        state.followSelected = false;
      }
    }
    render();
    if (!state.selectedTrip) fitToSelection(); // a tracked vehicle keeps the camera instead
    loadRouteHistogram(routeSelect.value);
    if (state.stopBox) pollStopBox(); // its note is about the line just changed
  };

  async function refresh() {
    try {
      const resp = await fetch('/api/positions');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      state.lastRows = await resp.json();
      state.positionsMs = latencyMs(resp);
      state.lastUpdated = Date.now();
      if (state.selectedTrip) {
        const v = state.lastRows.find(d => d.vehicleId === state.selectedTrip.vehicleId);
        if (v?.inService === false) {
          // finished its last trip and went out of service — nothing left to track
          statusEl.textContent =
            `Vehicle on line ${v.route} is not currently in service`;
          state.selectedTrip = null;
        } else if (v) {
          if (v.tripId !== state.selectedTrip.tripId
              || v.routeId !== state.selectedTrip.routeId) {
            // finished the trip and started the return leg — swap the shape
            state.selectedTrip = { vehicleId: v.vehicleId, routeId: v.routeId,
                                   tripId: v.tripId, path: null };
            loadTripPath(v);
          }
          if (state.followSelected) centerOnVehicle(v);
        } else {
          state.selectedTrip = null; // vehicle left the feed
          state.followSelected = false;
        }
      }
      updateRouteActivity(state.lastRows);
      render();
      globalThis._log.push({ t: Date.now(), ev: 'refresh', n: state.lastRows.length, zoom: map.getZoom() });
    } catch (err) {
      statusEl.textContent = `Refresh failed: ${err.message}`;
      globalThis._log.push({ t: Date.now(), ev: 'refresh-error', msg: err.message });
    }
  }

  map.on('zoomend', render); // toggles stop-layer visibility at the threshold

  // Redraws just fast enough for the halo pulse to read as smooth motion;
  // a no-op (skipped) whenever nothing is selected, so it costs nothing the
  // rest of the time.
  setInterval(() => { if (state.selectedTrip) render(); }, 100);

  await refresh();
  setInterval(refresh, REFRESH_MS);
  loadStops();
  loadRoutes();
}
