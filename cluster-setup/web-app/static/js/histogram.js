// The bottom-left "avg delay by hour" panel, drawn when a line is picked in
// the route dropdown. Self-contained: it owns one element and one endpoint,
// and nothing else on the map depends on it.

import { esc, getJson, hourKey, latencyBadgeHtml, hourlyBarsHtml, HOUR_TICKS_HTML }
  from './utils.js';
import { els } from './state.js';

// Interactive drill-down: selecting a route queries its whole-history,
// per-hour average delay in one shot (see app/pinot.py route_hourly_delay —
// deliberately uncached, so the badge shows Pinot's real query time, not a
// cache hit) and renders it as a small bar chart. Not fetched on every 10 s
// refresh: the underlying data barely moves within a session, only the
// selection does.
export async function loadRouteHistogram(route) {
  if (!route) {
    els.histogram.classList.add('hidden');
    els.histogram.innerHTML = '';
    return;
  }
  try {
    const { data, ms, stats } = await getJson(
      `/api/route-delay-histogram?route=${encodeURIComponent(route)}`);
    renderRouteHistogram(route, data, ms, stats);
  } catch (err) {
    els.histogram.classList.remove('hidden');
    els.histogram.innerHTML = `<h4>Route ${esc(route)} — delay by hour</h4>`
      + `Failed to load: ${esc(err.message)}`;
  }
}

function renderRouteHistogram(route, rows, ms, stats) {
  const byHour = new Map(rows.map(r => [r.hour, r]));
  // Always draw all 24 slots so a route with sparse-hour coverage (e.g. a
  // night line) still reads as a full day, not a squeezed partial chart.
  const hours = Array.from({ length: 24 },
    (_, h) => byHour.get(hourKey(h)) || null);
  const bars = hourlyBarsHtml(
    hours.map(r => r ? r.avgDelaySec : null),
    (v, h) => v == null ? `${h}:00 — no data`
      : `${h}:00 — avg ${v}s over ${byHour.get(hourKey(h)).snapshots} snapshots`);
  els.histogram.classList.remove('hidden');
  els.histogram.innerHTML =
    `<h4>Route ${esc(route)} — avg delay by hour (whole history)` +
    `${latencyBadgeHtml(ms, stats)}</h4>` +
    `<div class="bars">${bars}</div>` +
    `<div class="hours">${HOUR_TICKS_HTML}</div>`;
}
