import { esc, fmtBytes, getJson, hourKey, latencyBadgeHtml, hourlyBarsHtml,
         HOUR_TICKS_HTML } from './utils.js';

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
// reads as "points on the map", which it is not. Fed from refreshOverview's
// /api/stats fetch rather than its own: the endpoint is uncached, so one
// live stats query per tick serves both this strip and the Total-rows tile.
function renderStorageStrip(s, ms) {
  document.getElementById('dash-stats').innerHTML =
    `Apache Pinot storage (hybrid realtime + offline table): ` +
    `<b>${s.segments.toLocaleString()}</b> segments · ` +
    `<b>${fmtBytes(s.sizeBytes)}</b>` +
    latencyBadgeHtml(ms);
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
    // allSettled rather than all: the storage strip is worth painting even
    // when the rush-hour query is the one that failed — and awaiting the
    // first promise alone would leave the second's rejection unhandled.
    const [stats, rush] = await Promise.allSettled([
      getJson('/api/stats'),
      getJson('/api/network-hourly'),
    ]);
    if (stats.status === 'rejected') throw stats.reason;
    renderStorageStrip(stats.value.data, stats.value.ms);
    if (rush.status === 'rejected') throw rush.reason;
    renderOverview(el, stats.value.data, rush.value.data, rush.value.ms);
  } catch (err) {
    el.innerHTML =
      `<span class="ov-error">Overview failed to load: ${esc(err.message)}</span>`;
  }
}

function renderOverview(el, stats, rows, ms) {
  const byHour = new Map(rows.map(r => [r.hour, r.activeVehicles]));
  // All 24 slots always drawn, same as the route-delay histogram: hours with
  // no data (feed gaps) read as an empty slot, not a squeezed axis.
  const hours = Array.from({ length: 24 }, (_, h) => byHour.get(hourKey(h)) ?? 0);
  const bars = hourlyBarsHtml(hours, (v, h) => `${h}:00 — ${v} active vehicles`);
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
      `<div class="hours">${HOUR_TICKS_HTML}</div>` +
    `</div>`;
}
