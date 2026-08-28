// Everything deck.gl draws on top of the base map, and the tooltip it shows
// for a hovered feature. Layer construction only: what is drawn is decided
// here, when to redraw is map.js's business (render()).

import { colorForRoute, esc, routeOf, time24 } from './utils.js';
import { OFF_ROUTE_M, projectOnPath, nearestRouteStop, thinOverlapping } from './geo.js';
import { state, els } from './state.js';

const HALO_PERIOD_MS = 2200;

// Below this the stop poles are hidden as clutter (unless a line is selected,
// which shows its own stops at any zoom).
const STOP_MIN_ZOOM = 13;

// Are stop poles being drawn at all right now? The layer's own `visible` and
// the departures popup ask the same question — the popup points at a pole, so
// it closes whenever the poles go (zoomed out past the threshold, or switched
// to the heatmap). One expression so the two can never disagree.
export function stopsVisible() {
  return !els.heatmapToggle.checked
    && (Boolean(els.routeSelect.value) || state.map.getZoom() >= STOP_MIN_ZOOM);
}

function vehicleActionText(d) {
  if (d.inService === false) return 'Not currently in service';
  const verb = state.selectedTrip?.vehicleId === d.vehicleId ? 'hide' : 'show';
  return `Click to ${verb} the route path`;
}

export function tooltip({ object: d }) {
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
  return routeOf(d).length * 8;
}
function arrowOffset(d) {
  return [-(routeTextWidth(d) / 2 + 9), 0];
}

function heatmapLayers(heatOn) {
  if (!heatOn || !state.heatmapData.length) return [];
  return [new deck.HeatmapLayer({
    id: 'heatmap',
    data: state.heatmapData,
    getPosition: d => [d.lonCell, d.latCell],
    getWeight: d => d.pings,
    radiusPixels: 40,
    opacity: 0.55,
  })];
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
// Driven by a dedicated fast interval in map.js, separate from the 10 s data
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

// Zoomed out to the whole city ~300 badges pile into unreadable mush, so
// drop the ones that would overlap. Recomputed only when the data, camera or
// selection actually changes: render() also runs every 100 ms to animate the
// halo, and reprojecting every vehicle on each of those frames would be pure
// waste. thinOverlapping is in geo.js, unit-tested there.
let thinCache = { key: null, rows: null };
function thinVehicles(rows) {
  const c = state.map.getCenter();
  const sel = state.selectedTrip?.vehicleId ?? '';
  const key = `${state.lastUpdated}|${rows.length}|${state.map.getZoom().toFixed(3)}`
            + `|${c.lng.toFixed(5)},${c.lat.toFixed(5)}|${sel}`;
  if (thinCache.key === key) return thinCache.rows;
  const pts = rows.map(d => {
    const p = state.map.project([d.lon, d.lat]);
    return { x: p.x, y: p.y, priority: d.vehicleId === sel ? 1 : 0 };
  });
  const keep = thinOverlapping(pts);
  const out = rows.filter((_, i) => keep.has(i));
  thinCache = { key, rows: out };
  return out;
}

// The whole stack, bottom to top, for one render. `rows` is already filtered
// to the selected line; `route` and `heatOn` are the two mode switches that
// decide which layers are populated at all.
export function deckLayers(rows, route, heatOn) {
  // One decision, both vehicle layers: a badge and its arrow are one visual
  // unit and must appear or disappear together.
  const shown = heatOn ? rows : thinVehicles(rows);
  return [
    // Density heatmap — lowest layer, everything else reads on top of it.
    ...heatmapLayers(heatOn),
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
      visible: stopsVisible(),
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
      getText: routeOf,
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
  ];
}
