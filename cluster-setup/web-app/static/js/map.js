import { colorForRoute, time24, timeHM, esc, isTram, latencyMs, latencyBadgeHtml, elapsedClock } from './utils.js';
import { OFF_ROUTE_M, projectOnPath, nearestRouteStop } from './geo.js';

const REFRESH_MS = 30000;
const INITIAL_VIEW = { center: [18.6466, 54.352], zoom: 15 }; // Gdansk Old Town
const HEATMAP_TTL_MS = 300000;
const HALO_PERIOD_MS = 2200;

// Single app-state object rather than many top-level lets, so refresh/render
// interactions are easier to follow and functions can't accidentally read a
// stale closure variable. `progress` is added onto the selectedTrip object
// itself (see tripPathLayers) since it's per-selection, recomputed each render.
const state = {
  map: null,
  overlay: null,
  selectedTrip: null, // when set, has fields vehicleId, routeId, tripId, path, progress
  lastRows: [],
  lastUpdated: 0,
  positionsMs: null, // Pinot's timeUsedMs for the last /api/positions fetch
  heatmapMs: null,   // same, for the last /api/heatmap fetch
  stopsData: [],
  heatmapData: [],
  heatmapAt: 0,
};

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
           Click to ${state.selectedTrip?.vehicleId === d.vehicleId
                      ? 'hide' : 'show'} the route path`,
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
  map.addControl(new mapboxgl.NavigationControl(), 'top-right');
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

  // 24 h ping-density heatmap (server-cached Pinot aggregate). Fetched on
  // first toggle-on; re-fetched only when older than the server's cache TTL.
  async function loadHeatmap() {
    if (Date.now() - state.heatmapAt < HEATMAP_TTL_MS) return;
    try {
      const resp = await fetch('/api/heatmap');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      state.heatmapData = await resp.json();
      state.heatmapMs = latencyMs(resp);
      state.heatmapAt = Date.now();
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
  // cache hit) and renders it as a small bar chart. Not fetched on every 30 s
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
    state.selectedTrip = { vehicleId: d.vehicleId, routeId: d.routeId,
                           tripId: d.tripId, path: null };
    followVehicle(d);
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
    render();
  }

  // The split is recomputed from the fresh position on every render, so each
  // 30 s refresh advances the grey portion without re-fetching the geometry.
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
  // Driven by a dedicated fast interval (below), separate from the 30 s data
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

  async function showStopBox(info) {
    const { stopId, name } = info.object;
    stopBox.classList.remove('hidden');
    stopBox.innerHTML = `<h3>${esc(name)}</h3>Loading…`;
    // anchor near the click, clamped into the map view
    const view = document.getElementById('map-view');
    stopBox.style.left = Math.min(info.x + 12, view.clientWidth - 300) + 'px';
    stopBox.style.top = Math.min(info.y + 12, view.clientHeight - 200) + 'px';
    try {
      const resp = await fetch(`/api/departures?stopId=${encodeURIComponent(stopId)}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const dep = await resp.json();
      const today = new Date().toDateString();
      const rows = dep.departures.map(d => {
        const t = new Date(d.time);
        const day = t.toDateString() === today ? ''
          : t.toLocaleDateString([], { weekday: 'short' }) + ' ';
        // countdown runs on the live estimate when a vehicle reports a delay
        const eta = d.estimated ?? d.time;
        const mins = Math.max(0, Math.round((eta - Date.now()) / 60000));
        let delay = '';
        if (d.delayMin) {
          delay = d.delayMin > 0
            ? ` <span class="delay late">+${d.delayMin}</span>`
            : ` <span class="delay early">${d.delayMin}</span>`;
        }
        return `<tr><td>${day}${timeHM(d.time)}${delay}</td>
                    <td><span class="route-badge">${esc(d.route)}</span></td>
                    <td class="headsign">${esc(d.headsign)}</td>
                    <td>${mins} min</td></tr>`;
      }).join('');
      let label = info.object.routes.length
        ? 'No scheduled departures'
        : 'Stop not in use — no scheduled departures';
      if (dep.mode === 'hour') {
        label = 'Next 60 minutes';
      } else if (dep.departures.length) {
        label = 'No departures within an hour — next scheduled';
      }
      stopBox.innerHTML = `
        <button class="close" aria-label="Close">✕</button>
        <h3>${esc(name)}</h3>
        <div class="mode">${label}${latencyBadgeHtml(latencyMs(resp))}</div>
        <table>${rows}</table>`;
      stopBox.querySelector('.close').onclick = hideStopBox;
    } catch (err) {
      stopBox.innerHTML = `<h3>${esc(name)}</h3>Failed to load departures: ${esc(err.message)}`;
    }
  }

  function hideStopBox() {
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
      ['Buses', sorted.filter(r => !isTram(r))],
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

  function render() {
    const route = routeSelect.value;
    const rows = route
      ? state.lastRows.filter(d => String(d.route || '?') === route)
      : state.lastRows;
    // Heatmap (24 h aggregate) and live vehicles are alternate modes, not a
    // combined view — showing both at once buried the badges (and stop poles)
    // under the busiest hot spots, which are usually the same clusters.
    const heatOn = heatmapToggle.checked;
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
          data: heatOn ? [] : rows,
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
        // /api/positions (within 300 m of the trip's first/last stop). An
        // ordinary speed-0 stop at lights or a stop keeps the last heading.
        new deck.TextLayer({
          id: 'vehicle-arrows',
          data: heatOn ? [] : rows.filter(d => d.speed > 0 || !d.atTerminus),
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
    // The age span is refilled by its own ticking interval (see initMap),
    // not by render() — it needs to count up between refreshes, not just
    // whenever the vehicle layers happen to redraw.
    statusEl.innerHTML = `${count} vehicles · data age <span id="data-age"></span>`
      + latencyBadgeHtml(ms);
  }

  // While a single vehicle is tracked (clicked), the camera keeps it
  // centered on every refresh without changing zoom — panTo (not
  // flyTo/fitBounds) leaves the user's chosen zoom level alone, unlike the
  // whole-line fitToSelection below.
  function followVehicle(d) {
    map.panTo([d.lon, d.lat]);
  }

  // While a line is selected (and no single vehicle is tracked) the camera
  // follows it: fitted on selection and re-fitted after every refresh, so
  // the whole line stays in view as the vehicles move. With "All vehicles"
  // the refresh never moves the map.
  function fitToSelection() {
    const route = routeSelect.value;
    if (!route) {
      map.flyTo(INITIAL_VIEW);
      return;
    }
    const rows = state.lastRows.filter(d => String(d.route || '?') === route);
    if (!rows.length) return;
    const bounds = new mapboxgl.LngLatBounds();
    for (const d of rows) bounds.extend([d.lon, d.lat]);
    // maxZoom keeps a single-vehicle line from zooming into rooftop level.
    map.fitBounds(bounds, { padding: 80, maxZoom: 15 });
  }

  routeSelect.onchange = () => {
    // the drawn path belongs to one vehicle; drop it if the filter hides it
    if (state.selectedTrip && routeSelect.value) {
      const v = state.lastRows.find(d => d.vehicleId === state.selectedTrip.vehicleId);
      if (!v || String(v.route || '?') !== routeSelect.value) state.selectedTrip = null;
    }
    render();
    if (!state.selectedTrip) fitToSelection(); // a tracked vehicle keeps the camera instead
    loadRouteHistogram(routeSelect.value);
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
        if (v) {
          if (v.tripId !== state.selectedTrip.tripId
              || v.routeId !== state.selectedTrip.routeId) {
            // finished the trip and started the return leg — swap the shape
            state.selectedTrip = { vehicleId: v.vehicleId, routeId: v.routeId,
                                   tripId: v.tripId, path: null };
            loadTripPath(v);
          }
          followVehicle(v);
        } else {
          state.selectedTrip = null; // vehicle left the feed
        }
      }
      updateRouteActivity(state.lastRows);
      render();
      if (!state.selectedTrip && routeSelect.value) fitToSelection();
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

  // Ticks the status line's data-age clock independently of render() —
  // it needs to keep counting up between refreshes, not just jump once
  // every 30 s. Cheap: one span's textContent, not a layer rebuild.
  setInterval(() => {
    const el = document.getElementById('data-age');
    if (el) el.textContent = elapsedClock(state.lastUpdated);
  }, 100);

  await refresh();
  setInterval(refresh, REFRESH_MS);
  loadStops();
  loadRoutes();
}
