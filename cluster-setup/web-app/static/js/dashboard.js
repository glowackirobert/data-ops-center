import { fmtBytes } from './utils.js';

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
// show (the row count lives in the "Total rows" Big Number chart instead).
// Deliberately not in the top bar — a big row count next to the live map
// reads as "points on the map", which it is not.
export async function refreshStats() {
  try {
    const s = await (await fetch('/api/stats')).json();
    if (s.error) throw new Error(s.error);
    document.getElementById('dash-stats').innerHTML =
      `Apache Pinot storage (hybrid realtime + offline table): ` +
      `<b>${s.segments.toLocaleString()}</b> segments · ` +
      `<b>${fmtBytes(s.sizeBytes)}</b>`;
  } catch { /* strip stays as-is; next tick retries */ }
}
