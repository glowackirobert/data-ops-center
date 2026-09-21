import { esc, fmtBytes, getJson, hourKey, latencyBadgeHtml, hourlyBarsHtml,
         HOUR_TICKS_HTML } from './utils.js';

let dashboardEmbedded = false;

export async function initDashboard() {
  if (dashboardEmbedded) return;
  dashboardEmbedded = true;
  const status = document.getElementById('dash-status');
  try {
    const { data: first } = await getJson('/api/guest-token');
    await supersetEmbeddedSdk.embedDashboard({
      id: first.embeddedUuid,
      supersetDomain: first.supersetDomain,
      mountPoint: document.getElementById('dash-mount'),
      fetchGuestToken: async () => (await getJson('/api/guest-token')).data.token,
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

// Fed from refreshOverview's /api/stats fetch: one live query serves both
// this strip and the Total-rows tile.
function renderStorageStrip(s, ms, stats) {
  document.getElementById('dash-stats').innerHTML =
    `Apache Pinot storage (hybrid realtime + offline table): ` +
    `<b>${s.segments.toLocaleString()}</b> segments · ` +
    `<b>${fmtBytes(s.sizeBytes)}</b>` +
    latencyBadgeHtml(ms, stats);
}

// Native overview strip: both panels query Pinot through server.py and paint
// in tens of ms, while the embedded SDK is still booting Superset below.
export async function refreshOverview() {
  const el = document.getElementById('dash-overview');
  try {
    // allSettled: the storage strip is worth painting even when only the
    // rush-hour query failed.
    const [stats, rush] = await Promise.allSettled([
      getJson('/api/stats'),
      getJson('/api/network-hourly'),
    ]);
    if (stats.status === 'rejected') throw stats.reason;
    renderStorageStrip(stats.value.data, stats.value.ms, stats.value.stats);
    if (rush.status === 'rejected') throw rush.reason;
    renderOverview(el, stats.value.data, rush.value.data, rush.value.ms, rush.value.stats);
  } catch (err) {
    el.innerHTML =
      `<span class="ov-error">Overview failed to load: ${esc(err.message)}</span>`;
  }
}

function renderOverview(el, stats, rows, ms, rushStats) {
  const byHour = new Map(rows.map(r => [r.hour, r.activeVehicles]));
  // All 24 slots always drawn: a feed gap reads as an empty slot, not a squeezed axis.
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
      `${latencyBadgeHtml(ms, rushStats)}</h4>` +
      `<div class="bars">${bars}</div>` +
      `<div class="hours">${HOUR_TICKS_HTML}</div>` +
    `</div>`;
}
