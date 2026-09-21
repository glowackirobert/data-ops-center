// The map tab: creates the Mapbox map and the deck.gl overlay once, then
// owns the refresh loop, the vehicle selection and the camera. What gets
// drawn is layers.js; the departures popup is stopbox.js; the route-delay
// panel is histogram.js.

import { time24, isTram, isNightBus, getJson, routeOf,
         latencyBadgeHtml } from './utils.js';
import { needsRecentre } from './geo.js';
import { state, els } from './state.js';
import { tooltip, deckLayers } from './layers.js';
import { showStopBox, hideStopBox, positionStopBox } from './stopbox.js';
import { loadRouteHistogram } from './histogram.js';
import { initMapFilter } from './mapfilter.js';

const REFRESH_MS = 10000;
const INITIAL_VIEW = { center: [18.6466, 54.352], zoom: 15 }; // Gdansk Old Town

function rowsOnRoute(route) {
  return state.lastRows.filter(d => routeOf(d) === route);
}

// Separate from currentRows so syncSelection can test one vehicle.
function passesNlFilter(d) {
  const f = state.nlFilter;
  if (!f) return true;
  if (f.minDelaySec != null && d.delay < f.minDelaySec) return false;
  if (f.inServiceOnly && d.inService === false) return false;
  return true;
}

// The dropdown's route plus the Filter box's delay/in-service constraint,
// both applied together. Memoized: render() runs at 10 Hz while a vehicle
// is selected, and deck.gl re-lays-out every layer handed a new `data`
// array — so the same array must come back until an input really changes.
let rowsCache = { lastRows: null, route: null, nlFilter: null, rows: [] };

function currentRows() {
  const route = els.routeSelect.value;
  const { lastRows, nlFilter } = state;
  const c = rowsCache;
  if (c.lastRows !== lastRows || c.route !== route || c.nlFilter !== nlFilter) {
    const rows = route ? rowsOnRoute(route) : lastRows;
    rowsCache = { lastRows, route, nlFilter, rows: nlFilter ? rows.filter(passesNlFilter) : rows };
  }
  return rowsCache.rows;
}

// The map container is display:none while the Analytics tab is showing.
export function resizeMap() {
  state.map?.resize();
}

export async function initMap() {
  const { data: cfg } = await getJson('/api/config');
  mapboxgl.accessToken = cfg.mapboxToken;

  const map = new mapboxgl.Map({
    container: 'map',
    // light-v11 keeps the coloured markers legible. Force mercator: the
    // default globe projection breaks deck.gl overlay alignment and picking.
    style: 'mapbox://styles/mapbox/light-v11',
    projection: 'mercator',
    ...INITIAL_VIEW,
  });
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
      else clearTripPath({ restore: true }); // empty-map click clears the drawn route
    },
  });
  map.addControl(overlay);
  state.overlay = overlay;

  // Test seam for browser-driven verification (deck.gl picking, layer state).
  globalThis._map = map;
  globalThis._overlay = overlay;
  globalThis._log = [];
  map.on('moveend', () => globalThis._log.push(
    { t: Date.now(), ev: 'moveend', zoom: map.getZoom() }));

  const { status: statusEl, routeSelect, heatmapToggle } = els;

  // Last routine status line written; a direct write elsewhere resets it so
  // render() rewrites afterwards. Skipping unchanged writes matters at 10 Hz:
  // innerHTML rebuilds the latency badge node every time.
  let statusHtml = '';
  function showStatus(text) {
    statusEl.textContent = text;
    statusHtml = '';
  }

  // Queried live on every toggle-on, no caching, so the latency badge is
  // always a real query. Returns an error message for the caller's render().
  async function loadHeatmap() {
    try {
      const { data, ms, stats } = await getJson('/api/heatmap');
      state.heatmapData = data;
      state.heatmapMs = ms;
      state.heatmapStats = stats;
    } catch (err) {
      return `Heatmap failed: ${err.message}`;
    }
  }

  heatmapToggle.onchange = async () => {
    let error;
    if (heatmapToggle.checked) {
      error = await loadHeatmap();
      clearTripPath(); // heatmap and live vehicles are separate modes
    }
    render(error);
  };

  async function selectVehicle(d) {
    if (state.selectedTrip?.vehicleId === d.vehicleId) {
      clearTripPath({ restore: true });
      return;
    }
    if (d.inService === false) {
      showStatus(`Vehicle on line ${d.route} is not currently in service`);
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
      const { data } = await getJson(
        `/api/route-shape?routeId=${d.routeId}&tripId=${d.tripId}`);
      // clicks can race the fetch; apply only if this vehicle is still selected
      if (state.selectedTrip?.vehicleId === d.vehicleId) {
        state.selectedTrip.path = data.path;
        render();
      }
    } catch (err) {
      // 404 is the ordinary case: between trips, or a course not in today's plan.
      const message = err.status === 404
        ? `No route path for line ${d.route} (course ${d.tripId})`
        : `Route path failed: ${err.message}`;
      clearTripPath();
      render(message);
    }
  }

  // `restore` marks a deliberate deselect (re-click, empty-map click): the
  // selection was an excursion inside the dropdown's scope, so hand the
  // camera back to that scope — but only while the follow was still on; once
  // the user has panned or zoomed the camera is theirs. Error paths and the
  // heatmap toggle pass nothing: they are not a request to look elsewhere.
  function clearTripPath({ restore = false } = {}) {
    if (!state.selectedTrip) return;
    const wasFollowing = state.followSelected;
    state.selectedTrip = null;
    state.followSelected = false;
    render();
    if (restore && wasFollowing) fitToSelection();
  }

  // The server answers 503 while it is still parsing the GTFS feed — retry.
  async function loadStops() {
    try {
      const { data } = await getJson('/api/stops');
      state.stopsData = data;
      render();
    } catch {
      setTimeout(loadStops, 10000);
    }
  }

  // Every camera frame, so the popup travels with the drag. No argument:
  // the event must not arrive as a truthy `measure`.
  map.on('move', () => positionStopBox());

  // The full GTFS route list, not just routes with a live vehicle, so idle
  // and night lines stay selectable. 503s until the feed is parsed — retry.
  async function loadRoutes() {
    try {
      const { data } = await getJson('/api/routes');
      buildRouteOptions(data);
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

  // Only toggles a class per refresh — the options are never rebuilt, so an
  // open dropdown or the user's selection is never disturbed.
  function updateRouteActivity(rows) {
    const active = new Set(rows.map(routeOf));
    for (const opt of routeSelect.querySelectorAll('option[value]:not([value=""])')) {
      opt.classList.toggle('route-inactive', !active.has(opt.value));
    }
  }

  // Called from the 10 s refresh, from every state change that alters what
  // is drawn, and every 100 ms while a vehicle is selected (halo pulse).
  // `statusOverride` replaces the vehicle-count line: a caller that set a
  // message moments earlier in the same synchronous handler would otherwise
  // see this render() paint over it before the browser ever paints.
  function render(statusOverride) {
    const route = routeSelect.value;
    const rows = currentRows();
    // Heatmap and live vehicles are alternate modes — together, the hot
    // spots buried the badges.
    const heatOn = heatmapToggle.checked;
    overlay.setProps({ layers: deckLayers(rows, route, heatOn) });
    if (statusOverride) {
      showStatus(statusOverride);
      return;
    }
    const filtered = Boolean(route) || Boolean(state.nlFilter);
    const count = filtered ? `${rows.length} of ${state.lastRows.length}` : `${rows.length}`;
    const ms = heatOn ? state.heatmapMs : state.positionsMs;
    const stats = heatOn ? state.heatmapStats : state.positionsStats;
    const html = `${count} vehicles · updated ${time24(state.lastUpdated)}`
      + latencyBadgeHtml(ms, stats);
    if (html !== statusHtml) {
      statusEl.innerHTML = html;
      statusHtml = html;
    }
  }

  // panTo, never flyTo/fitBounds: selecting a vehicle is not a request to
  // re-frame the map, so zoom is untouched. `force` is the click that selects;
  // refresh ticks go through the deadzone instead, so a vehicle creeping
  // across mid-screen costs no camera movement.
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

  // The user's own pan/zoom ends the follow — undoing it on the next refresh
  // would be worse than losing the vehicle off-frame. Mapbox marks user
  // gestures with originalEvent; our own panTo has none.
  map.on('movestart', e => { if (e.originalEvent) state.followSelected = false; });

  // Fits once, at the moment a line is picked (and when a selection ends —
  // see clearTripPath); never from refresh(), or it would keep snapping the
  // camera back. "All vehicles" moves nothing: it names no place to look.
  function fitToSelection() {
    const route = routeSelect.value;
    if (!route) return;
    const rows = rowsOnRoute(route);
    const stops = state.stopsData.filter(s => s.routes.includes(route));
    if (!rows.length && !stops.length) return;
    const bounds = new mapboxgl.LngLatBounds();
    for (const d of rows) bounds.extend([d.lon, d.lat]);
    // Include the stops so a line whose vehicles are bunched on one segment
    // still shows end to end.
    for (const s of stops) bounds.extend([s.lon, s.lat]);
    map.fitBounds(bounds, { padding: 80, maxZoom: 15 }); // no rooftop zoom on a single vehicle
  }

  routeSelect.onchange = e => {
    // A manual pick takes filtering scope back from the Filter box, whose
    // delay/in-service constraint would otherwise keep hiding vehicles with
    // no indicator once the box is closed. applyMapFilter dispatches this
    // same event itself (e.isTrusted false) to drive the dropdown from a
    // sentence, so only a real user pick clears nlFilter.
    if (e.isTrusted && state.nlFilter) {
      state.nlFilter = null;
      clearMapFilterResult();
    }
    const route = routeSelect.value;
    // the drawn path belongs to one vehicle; drop it if the filter hides it
    if (state.selectedTrip && route) {
      const v = state.lastRows.find(d => d.vehicleId === state.selectedTrip.vehicleId);
      if (!v || routeOf(v) !== route) {
        state.selectedTrip = null;
        state.followSelected = false;
      }
    }
    render();
    // Picking a line always fits, even to the tracked vehicle's own line. The
    // selection survives but the follow ends, or the next refresh would pan
    // straight back and undo the fit.
    if (route) {
      state.followSelected = false;
      fitToSelection();
    }
    loadRouteHistogram(route);
    hideStopBox(); // picking a line is a new question; the popup answered the old one
  };

  // A route or heatmap named in the sentence drives the dropdown/checkbox
  // through their own onchange handlers, so there is one filtering path.
  // Delay/in-service have no click equivalent and live in state.nlFilter.
  // Only ever adds: a sentence naming no route leaves the dropdown alone,
  // and the heatmap is never turned off.
  function applyMapFilter(filter) {
    state.nlFilter = (filter.minDelaySec != null || filter.inServiceOnly)
      ? { minDelaySec: filter.minDelaySec, inServiceOnly: filter.inServiceOnly }
      : null;
    if (filter.routes.length) {
      routeSelect.value = filter.routes[0];
      routeSelect.dispatchEvent(new Event('change'));
    }
    if (filter.heatmap && !heatmapToggle.checked) {
      heatmapToggle.checked = true;
      heatmapToggle.dispatchEvent(new Event('change'));
    }
    if (filter.placeMatch) {
      const { bbox } = filter.placeMatch;
      const bounds = new mapboxgl.LngLatBounds(
        [bbox.lonMin, bbox.latMin], [bbox.lonMax, bbox.latMax]);
      map.fitBounds(bounds, { padding: 80, maxZoom: 16 });
    }
    render();
  }
  // Wired before the first refresh() below: the Filter box must stay usable
  // while /api/positions is pending or timing out.
  const { clearResult: clearMapFilterResult } = initMapFilter(applyMapFilter);

  // Reconcile the tracked vehicle with the poll that just landed. Returns a
  // status message when it just went out of service or fell out of the
  // filter, for refresh()'s render() to show.
  function syncSelection(rows) {
    const trip = state.selectedTrip;
    if (!trip) return;
    const v = rows.find(d => d.vehicleId === trip.vehicleId);
    if (!v) { // left the feed
      state.selectedTrip = null;
      state.followSelected = false;
      return;
    }
    if (v.inService === false) {
      state.selectedTrip = null;
      return `Vehicle on line ${v.route} is not currently in service`;
    }
    if (!passesNlFilter(v)) {
      // currentRows() no longer draws it, so there is nothing to track and
      // the camera must stop following a position nothing is drawn at.
      state.selectedTrip = null;
      state.followSelected = false;
      return `Vehicle on line ${v.route} no longer matches the active filter`;
    }
    if (v.tripId !== trip.tripId || v.routeId !== trip.routeId) {
      // finished the trip and started the return leg — swap the shape
      state.selectedTrip = { vehicleId: v.vehicleId, routeId: v.routeId,
                             tripId: v.tripId, path: null };
      loadTripPath(v);
    }
    if (state.followSelected) centerOnVehicle(v);
  }

  async function refresh() {
    try {
      const { data, ms, stats } = await getJson('/api/positions');
      state.lastRows = data;
      state.positionsMs = ms;
      state.positionsStats = stats;
      state.lastUpdated = Date.now();
      const statusOverride = syncSelection(state.lastRows);
      updateRouteActivity(state.lastRows);
      render(statusOverride);
      globalThis._log.push({ t: Date.now(), ev: 'refresh', n: state.lastRows.length, zoom: map.getZoom() });
    } catch (err) {
      showStatus(`Refresh failed: ${err.message}`);
      globalThis._log.push({ t: Date.now(), ev: 'refresh-error', msg: err.message });
    }
  }

  // Not `map.on('zoomend', render)`: render treats a truthy argument as a
  // status string, and the event object would print as "[object Object]".
  map.on('zoomend', () => render()); // toggles stop-layer visibility at the threshold

  // Halo pulse; skipped whenever nothing is selected.
  setInterval(() => { if (state.selectedTrip) render(); }, 100);

  await refresh();
  setInterval(refresh, REFRESH_MS);
  loadStops();
  loadRoutes();
}
