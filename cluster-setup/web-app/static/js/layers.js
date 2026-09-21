// Everything deck.gl draws on top of the base map, and the tooltip it shows
// for a hovered feature. Layer construction only; when to redraw is map.js's
// business (render()).

import { colorForRoute, esc, routeOf, time24 } from './utils.js';
import { OFF_ROUTE_M, projectOnPath, nearestRouteStop } from './geo.js';
import { state, els } from './state.js';

const HALO_PERIOD_MS = 2200;

// Below this the stop poles are hidden as clutter (unless a line is selected,
// which shows its own stops at any zoom).
const STOP_MIN_ZOOM = 13;

const NONE = [];

// Are stop poles drawn at all right now? The layer's `visible` and the
// departures popup ask the same question — the popup points at a pole, so it
// closes whenever the poles go. One expression so the two can never disagree.
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

// Marker layout, anchored on the vehicle position:  [ ↑ route ]
// The arrow sits in the box's left backgroundPadding; its offset tracks
// half the route text width (~8 px per glyph at size 14 bold).
function routeTextWidth(d) {
  return routeOf(d).length * 8;
}
function arrowOffset(d) {
  return [-(routeTextWidth(d) / 2 + 9), 0];
}

// deck.gl diffs a layer's `data` by reference: a fresh array makes it re-lay-
// out every glyph and re-upload its buffers. render() runs at 10 Hz while a
// vehicle is selected, so each derived array below is handed back unchanged
// until its inputs change — only the halo layer is rebuilt every tick.
let stopsCache = { stopsData: null, route: null, stops: NONE };
function stopsFor(route) {
  const { stopsData } = state;
  const c = stopsCache;
  if (c.stopsData !== stopsData || c.route !== route) {
    stopsCache = { stopsData, route,
                   stops: route ? stopsData.filter(s => s.routes.includes(route)) : stopsData };
  }
  return stopsCache.stops;
}

let arrowsCache = { rows: null, arrows: NONE };
function arrowsFor(rows) {
  if (arrowsCache.rows !== rows) {
    arrowsCache = { rows, arrows: rows.filter(d => d.speed > 0 || !d.atTerminus) };
  }
  return arrowsCache.arrows;
}

function heatmapLayers(heatOn) {
  if (!heatOn || !state.heatmapData.length) return NONE;
  return [new deck.HeatmapLayer({
    id: 'heatmap',
    data: state.heatmapData,
    getPosition: d => [d.lonCell, d.latCell],
    getWeight: d => d.pings,
    radiusPixels: 40,
    opacity: 0.55,
  })];
}

// The split is recomputed from the fresh position on each refresh, so the
// grey portion advances without re-fetching the geometry. The projected
// point closes both halves, so the colour changes exactly at the vehicle.
let splitCache = { trip: null, path: null, rows: null, stopsData: null, layers: NONE };
function tripPathLayers(rows) {
  const trip = state.selectedTrip;
  const { stopsData } = state;
  const c = splitCache;
  if (c.trip === trip && c.path === trip?.path && c.rows === rows && c.stopsData === stopsData) {
    return c.layers;
  }
  splitCache = { trip, path: trip?.path, rows, stopsData, layers: buildTripPathLayers(trip, rows) };
  return splitCache.layers;
}

function buildTripPathLayers(trip, rows) {
  if (!trip?.path || trip.path.length < 2) return NONE;
  const v = rows.find(d => d.vehicleId === trip.vehicleId);
  if (!v) return NONE;
  let proj = projectOnPath(trip.path, v, trip.progress);
  const onRoute = proj.meters <= OFF_ROUTE_M;
  if (onRoute) {
    trip.progress = proj.progress;
  } else {
    // Off-route (e.g. at a depot): nothing is covered yet — anchor the line
    // at this route's stop nearest the vehicle, where the trip will start.
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

// Pulsing ring under the selected vehicle's badge; pixel-sized so it reads
// the same at any zoom. The only layer that changes on the 100 ms tick.
function selectedVehicleHaloLayer(rows) {
  if (!state.selectedTrip) return NONE;
  const v = rows.find(d => d.vehicleId === state.selectedTrip.vehicleId);
  if (!v) return NONE;
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

// The whole stack, bottom to top. `rows` is already filtered (see
// currentRows in map.js); `route` and `heatOn` decide which layers are
// populated at all.
export function deckLayers(rows, route, heatOn) {
  return [
    ...heatmapLayers(heatOn),
    // Trajectory under stops and vehicles (path layers are not pickable).
    ...(heatOn ? NONE : tripPathLayers(rows)),
    // Stops under vehicles so vehicles win picking conflicts. Hidden zoomed
    // out and in heatmap mode; with a line selected, only its stops, at any
    // zoom, since fitting a long line can land below the threshold.
    new deck.ScatterplotLayer({
      id: 'stops',
      data: stopsFor(route),
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
    ...(heatOn ? NONE : selectedVehicleHaloLayer(rows)),
    // Route-number box; the text background is the box, with extra left
    // padding reserving room for the heading arrow.
    new deck.TextLayer({
      id: 'vehicles',
      data: heatOn ? NONE : rows,
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
    // Heading arrow. Hidden only for a vehicle standing at a terminus of its
    // own course (atTerminus from /api/positions); an ordinary speed-0 stop
    // keeps the last heading.
    new deck.TextLayer({
      id: 'vehicle-arrows',
      data: heatOn ? NONE : arrowsFor(rows),
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
