// The map tab: creates the Mapbox map and the deck.gl overlay once, then
// owns the refresh loop, the vehicle selection and the camera. What gets
// drawn is layers.js; the departures popup is stopbox.js; the route-delay
// panel is histogram.js.

import { time24, isTram, isNightBus, getJson, routeOf, latencyBadgeHtml }
  from './utils.js';
import { needsRecentre } from './geo.js';
import { state, els } from './state.js';
import { tooltip, deckLayers } from './layers.js';
import { showStopBox, hideStopBox, positionStopBox } from './stopbox.js';
import { loadRouteHistogram } from './histogram.js';

const REFRESH_MS = 10000;
const INITIAL_VIEW = { center: [18.6466, 54.352], zoom: 15 }; // Gdansk Old Town

// The latest positions on one line — what picking a line in the dropdown
// means, shared by the renderer and the camera fit.
function rowsOnRoute(route) {
  return state.lastRows.filter(d => routeOf(d) === route);
}

// Called when the map tab becomes visible again — its container was
// display:none while hidden, so Mapbox needs telling its size may have
// changed.
export function resizeMap() {
  state.map?.resize();
}

export async function initMap() {
  const { data: cfg } = await getJson('/api/config');
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
      else clearTripPath({ restore: true }); // empty-map click clears the drawn route
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

  // Short names for the three nodes this module writes to; the other
  // modules reach the same ones through els.
  const { status: statusEl, routeSelect, heatmapToggle } = els;

  // 24 h ping-density heatmap, queried live from Pinot on every toggle-on —
  // no caching anywhere, so the latency badge always shows a real query.
  // Returns an error message on failure, so the caller's render() can show
  // it instead of the routine vehicle-count line — see render()'s comment.
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

  // Clicking a vehicle draws the trajectory of the trip it is serving, split
  // at the vehicle: covered part grey, part ahead light blue. Clicking the
  // same vehicle again, another vehicle, or empty map clears/replaces it.
  async function selectVehicle(d) {
    if (state.selectedTrip?.vehicleId === d.vehicleId) {
      clearTripPath({ restore: true });
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
      const { data } = await getJson(
        `/api/route-shape?routeId=${d.routeId}&tripId=${d.tripId}`);
      // clicks can race the fetch; apply only if this vehicle is still selected
      if (state.selectedTrip?.vehicleId === d.vehicleId) {
        state.selectedTrip.path = data.path;
        render();
      }
    } catch (err) {
      // 404 is the ordinary case — a vehicle between trips, or a course not
      // in today's plan. A fact to report, not a failure to apologise for.
      const message = err.status === 404
        ? `No route path for line ${d.route} (course ${d.tripId})`
        : `Route path failed: ${err.message}`;
      clearTripPath();
      render(message);
    }
  }

  // `restore` marks the deliberate deselects — re-clicking the tracked vehicle,
  // or clicking empty map. Selecting a vehicle is an excursion inside whatever
  // scope the dropdown defines, so ending it should hand the camera back to
  // that scope; otherwise the user is stranded wherever the follow drifted to.
  // Only when the follow was still on, though: once they have panned or zoomed
  // themselves the camera is theirs, the same rule `movestart` already applies.
  // Re-fitting beats restoring a saved viewport — after a few minutes the
  // vehicles have moved, and a stale centre/zoom can frame empty road.
  // The error paths (no trip shape, failed fetch) and the heatmap toggle pass
  // nothing: those are not the user asking to look somewhere else.
  function clearTripPath({ restore = false } = {}) {
    if (!state.selectedTrip) return;
    const wasFollowing = state.followSelected;
    state.selectedTrip = null;
    state.followSelected = false;
    render();
    if (restore && wasFollowing) fitToSelection();
  }

  // Stops are static for the day; fetched once. The server answers 503 for
  // the first seconds after start while it parses the GTFS feed — retry.
  async function loadStops() {
    try {
      const { data } = await getJson('/api/stops');
      state.stopsData = data;
      render();
    } catch {
      setTimeout(loadStops, 10000);
    }
  }

  // Every camera frame, not just moveend: the popup has to travel with the
  // drag, not teleport when it ends. The listener takes no argument — the
  // move event must not arrive as a truthy `measure`.
  map.on('move', () => positionStopBox());

  // Route list is the full GTFS schedule, fetched once — not just whichever
  // routes happen to have a live vehicle this refresh — so night lines and
  // temporarily idle routes stay selectable instead of disappearing. The
  // server 503s until the GTFS feed finishes parsing; retry.
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

  // Routes options are built once (above) and never rebuilt, so this only
  // toggles a class per refresh — an open dropdown or the user's selection
  // is never disturbed. Routes with no vehicle in the latest poll (idle
  // right now, not gone from the schedule) render greyed out, still
  // selectable — see .route-inactive in styles.css.
  function updateRouteActivity(rows) {
    const active = new Set(rows.map(routeOf));
    for (const opt of routeSelect.querySelectorAll('option[value]:not([value=""])')) {
      opt.classList.toggle('route-inactive', !active.has(opt.value));
    }
  }

  // Markers jump to the newly scraped position on each refresh — no
  // interpolated movement between API polls. Only the layers are replaced;
  // deck.gl diffs them on the GPU and the base map keeps its tiles, camera
  // and WebGL context. Called from the 10 s refresh, from every state change
  // that alters what is drawn, and every 100 ms while a vehicle is selected
  // (the halo pulse). `statusOverride`, when given, replaces the usual
  // vehicle-count line — for a status a caller set moments earlier in the
  // same synchronous handler (a heatmap failure, a lost route path, a
  // vehicle going out of service mid-poll) that this render() call would
  // otherwise paint over unseen, since nothing yields to the browser
  // between the two writes.
  function render(statusOverride) {
    const route = routeSelect.value;
    const rows = route ? rowsOnRoute(route) : state.lastRows;
    // Heatmap (24 h aggregate) and live vehicles are alternate modes, not a
    // combined view — showing both at once buried the badges (and stop poles)
    // under the busiest hot spots, which are usually the same clusters.
    const heatOn = heatmapToggle.checked;
    overlay.setProps({ layers: deckLayers(rows, route, heatOn) });
    if (statusOverride) {
      statusEl.textContent = statusOverride;
      return;
    }
    const count = route ? `${rows.length} of ${state.lastRows.length}` : `${rows.length}`;
    const ms = heatOn ? state.heatmapMs : state.positionsMs;
    const stats = heatOn ? state.heatmapStats : state.positionsStats;
    statusEl.innerHTML = `${count} vehicles · updated ${time24(state.lastUpdated)}`
      + latencyBadgeHtml(ms, stats);
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

  // Camera fit for the moment a line is picked in the dropdown, and for
  // handing the camera back when a selection ends (clearTripPath). Never
  // called from refresh(): once the user has the line in view they may
  // pan/zoom freely, and a 10 s re-fit would keep snapping the camera back.
  //
  // "All vehicles" deliberately moves nothing. Picking a line names a place
  // to look; "All vehicles" only widens the filter — every vehicle on screen
  // stays put and more appear around it, so there is nothing to bring into
  // view. Flying to INITIAL_VIEW here was the one camera move in this app
  // that answered no question the user had asked: it teleported someone
  // watching Osowa back to the Old Town, mid-track if a vehicle was selected.
  // That viewport belongs to page load, not to a filter.
  function fitToSelection() {
    const route = routeSelect.value;
    if (!route) return;
    const rows = rowsOnRoute(route);
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
    // Picking a line is an explicit "show me this line", so it always fits —
    // including when the tracked vehicle happens to be on that very line,
    // which used to be the one case that silently did nothing. The selection
    // survives (its trajectory is still worth seeing) but the follow does not,
    // or the next refresh would pan straight back and undo the fit. That fit
    // still holds the tracked vehicle: it is one of the points fitted.
    // "All vehicles" names no place to look, so it commands no camera and
    // ends no follow — see fitToSelection.
    if (route) {
      state.followSelected = false;
      fitToSelection();
    }
    loadRouteHistogram(route);
    // Not a placement problem any more — the popup tracks its stop's
    // coordinates and would ride the fit out, closing itself if the stop
    // leaves the screen. It closes here because picking a line is a new
    // question, and one stop's departures are the answer to the old one.
    hideStopBox();
  };

  // Bring the tracked vehicle up to date with the poll that just landed.
  // Three things can have happened to it since the last one: it went out of
  // service, it finished its trip and started the return leg (new course, so
  // a new shape to draw), or it dropped out of the feed entirely.
  // Returns a status message when the tracked vehicle just went out of
  // service, so refresh()'s render() can show it instead of the routine
  // vehicle-count line — see render()'s comment.
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
      // finished its last trip and went out of service — nothing left to track
      state.selectedTrip = null;
      return `Vehicle on line ${v.route} is not currently in service`;
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
