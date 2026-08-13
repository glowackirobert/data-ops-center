import { esc, fmtBytes, latencyMs, latencyBadgeHtml } from './utils.js';

let dashboardEmbedded = false;

export async function initDashboard() {
  if (dashboardEmbedded) return;
  dashboardEmbedded = true;
  const status = document.getElementById('dash-status');
  try {
    const first = await (await fetch('/api/guest-token')).json();
    if (first.error) throw new Error(first.error);
    await supersetEmbeddedSdk.embedDashboard({
      id: first.embeddedUuid,
      supersetDomain: first.supersetDomain,
      mountPoint: document.getElementById('dash-mount'),
      fetchGuestToken: async () =>
        (await (await fetch('/api/guest-token')).json()).token,
      dashboardUiConfig: {
        hideTitle: true,
        hideTab: true,
        hideChartControls: true, // no three-dot menu (edit chart, download, …)
        // keep the filter bar open so the report date-range picker is visible
        filters: { expanded: true },
      },
    });
    status.remove();
  } catch (err) {
    dashboardEmbedded = false; // allow retry on next tab switch
    status.textContent = `Failed to load dashboard: ${err.message}`;
    status.classList.add('error');
  }
}

// Storage strip on the Analytics tab: Pinot internals the dashboard can't
// show (the row count lives in the overview strip's Total rows tile instead).
// Deliberately not in the top bar — a big row count next to the live map
// reads as "points on the map", which it is not.
export async function refreshStats() {
  try {
    const resp = await fetch('/api/stats');
    const s = await resp.json();
    if (s.error) throw new Error(s.error);
    document.getElementById('dash-stats').innerHTML =
      `Apache Pinot storage (hybrid realtime + offline table): ` +
      `<b>${s.segments.toLocaleString()}</b> segments · ` +
      `<b>${fmtBytes(s.sizeBytes)}</b>` +
      latencyBadgeHtml(latencyMs(resp));
  } catch { /* strip stays as-is; next tick retries */ }
}

// Native overview strip (Total rows + Network rush hour — formerly the
// embedded Superset "Overview" dashboard): both panels query Pinot through
// server.py, so they paint in tens of milliseconds while the embedded SDK is
// still booting Superset's frontend bundle inside the main iframe below. The
// main dashboard stays embedded — its filters and chart tooling are worth the
// load time; these two always-on-screen numbers are not.
export async function refreshOverview() {
  const el = document.getElementById('dash-overview');
  try {
    const [statsResp, rushResp] = await Promise.all([
      fetch('/api/stats'),
      fetch('/api/network-hourly'),
    ]);
    const stats = await statsResp.json();
    const rush = await rushResp.json();
    if (stats.error) throw new Error(stats.error);
    if (rush.error) throw new Error(rush.error);
    renderOverview(el, stats, rush, latencyMs(rushResp));
  } catch (err) {
    el.innerHTML =
      `<span class="ov-error">Overview failed to load: ${esc(err.message)}</span>`;
  }
}

function renderOverview(el, stats, rows, ms) {
  const byHour = new Map(rows.map(r => [r.hour, r.activeVehicles]));
  // All 24 slots always drawn, same as the route-delay histogram: hours with
  // no data (feed gaps) read as an empty slot, not a squeezed axis.
  const hours = Array.from({ length: 24 }, (_, h) =>
    byHour.get(`${String(h).padStart(2, '0')}:00`) ?? 0);
  const max = Math.max(...hours, 1);
  const bars = hours.map((v, h) =>
    `<div class="bar" style="height:${Math.max(Math.round((v / max) * 100), 2)}%" ` +
    `title="${h}:00 — ${v} active vehicles"></div>`).join('');
  const ticks = [0, 6, 12, 18].map(h => `<span>${h}:00</span>`).join('');
  el.innerHTML =
    `<div class="ov-panel" id="overview-total">` +
      `<h4>Total GPS snapshots</h4>` +
      `<div class="ov-big">${stats.totalDocs.toLocaleString()}</div>` +
      `<div class="ov-sub">rows in Pinot (realtime + offline)</div>` +
    `</div>` +
    `<div class="ov-panel" id="overview-rush">` +
      `<h4>Network rush hour — active vehicles by hour (last 24 h)` +
      `${latencyBadgeHtml(ms)}</h4>` +
      `<div class="bars">${bars}</div>` +
      `<div class="hours">${ticks}</div>` +
    `</div>`;
}
