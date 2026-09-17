// The two singletons the map view shares: its app state, and the handful of
// DOM nodes it writes to. They live here rather than in map.js so that
// layers.js, stopbox.js and histogram.js can reach them directly — an ES
// module is a singleton, so every importer sees the same two objects, and
// map.js does not have to hand them down through every call.

// Single app-state object rather than many top-level lets, so refresh/render
// interactions are easier to follow and functions can't accidentally read a
// stale closure variable. `progress` is added onto the selectedTrip object
// itself (see tripPathLayers) since it's per-selection, recomputed each render.
export const state = {
  map: null,
  overlay: null,
  selectedTrip: null, // when set, has fields vehicleId, routeId, tripId, path, progress
  followSelected: false, // camera tracks selectedTrip until the user moves the map
  // when open: { stopId, name, lon, lat, routes, data, ms, stats, w, h, tick, poll }
  stopBox: null,
  lastRows: [],
  lastUpdated: 0,
  positionsMs: null,     // Pinot's timeUsedMs for the last /api/positions fetch
  positionsStats: null,  // and the explain-panel scan figures behind it
  heatmapMs: null,       // same, for the last /api/heatmap fetch
  heatmapStats: null,
  stopsData: [],
  heatmapData: [],
  // Set only by the Filter box (AI_PLATFORM_PLAN.md Track 2) — an extra,
  // additive constraint layered on top of whatever the route dropdown
  // already shows. null means "no extra constraint". Replaced wholesale on
  // every filter-box submission (each sentence describes the whole extra
  // filter, not a delta); never touched by the dropdown/heatmap checkbox,
  // which own their own state and are only ever added to, never reset, by
  // the filter box — see applyMapFilter in map.js.
  nlFilter: null,
};

// Resolved once, at import time: module scripts are deferred, so the document
// is already parsed. Every node here outlives the page, so there is nothing
// to re-query.
export const els = {
  status: document.getElementById('panel-status'),
  routeSelect: document.getElementById('route-filter'),
  stopBox: document.getElementById('stop-box'),
  heatmapToggle: document.getElementById('heatmap-toggle'),
  histogram: document.getElementById('route-histogram'),
  mapFilterBtn: document.getElementById('map-filter-btn'),
  mapFilterBox: document.getElementById('map-filter-box'),
  mapFilterForm: document.getElementById('map-filter-form'),
  mapFilterInput: document.getElementById('map-filter-input'),
  mapFilterResult: document.getElementById('map-filter-result'),
  // #map is the Mapbox container; map.project() returns pixels relative to
  // the canvas Mapbox puts inside it, which is inset within #map-view.
  mapView: document.getElementById('map-view'),
  mapContainer: document.getElementById('map'),
};
