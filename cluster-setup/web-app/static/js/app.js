import { initMap, resizeMap } from './map.js';
import { initDashboard, refreshOverview } from './dashboard.js';
import { initHelp } from './help.js';
import { initChat } from './chat.js';
import { explainReading } from './utils.js';

const dashView = document.getElementById('dash-view');

function showTab(name) {
  const isMap = name === 'map';
  document.getElementById('map-view').classList.toggle('hidden', !isMap);
  dashView.classList.toggle('hidden', isMap);
  document.getElementById('tab-map').classList.toggle('active', isMap);
  document.getElementById('tab-dash').classList.toggle('active', !isMap);
  if (isMap) {
    resizeMap(); // container was display:none while hidden
  } else {
    initDashboard();
    refreshOverview();
  }
}

document.getElementById('tab-map').onclick = () => showTab('map');
document.getElementById('tab-dash').onclick = () => showTab('dash');

// Explain popover for any latency badge carrying scan stats. One delegated
// listener: badges are rebuilt into fresh nodes on every panel refresh.
let openPopover = null;

function closePopover() {
  openPopover?.remove();
  openPopover = null;
}

function statRow(label, used, total) {
  return `<div>${label}: <b>${used.toLocaleString()}</b> / ${total.toLocaleString()}</div>`;
}

function openPopoverFor(badge) {
  const stats = JSON.parse(badge.dataset.stats);
  const pop = document.createElement('div');
  pop.className = 'latency-popover';
  pop.innerHTML =
    `<div class="reading">${explainReading(stats) ?? 'no scan data'}</div>` +
    statRow('rows scanned', stats.docsScanned, stats.totalDocs) +
    statRow('segments touched', stats.segmentsProcessed, stats.segmentsQueried) +
    `<div>${stats.segmentsPruned} pruned by value ·
     ${stats.serversQueried} server${stats.serversQueried === 1 ? '' : 's'} queried</div>`;
  document.body.append(pop);
  const r = badge.getBoundingClientRect();
  pop.style.left = Math.max(4, Math.min(r.left, window.innerWidth - pop.offsetWidth - 4)) + 'px';
  pop.style.top = (r.bottom + 4) + 'px';
  pop._badge = badge;
  openPopover = pop;
}

document.addEventListener('click', e => {
  const badge = e.target.closest('.latency-badge[data-stats]');
  if (badge) {
    const wasOpenForThisBadge = openPopover?._badge === badge;
    closePopover();
    if (!wasOpenForThisBadge) openPopoverFor(badge);
    return;
  }
  if (!e.target.closest('.latency-popover')) closePopover();
});

initHelp();
initChat();

// The overview strip is refetched on every switch to Analytics and then
// every 60 s while that tab is showing — not while the map is up, where two
// Pinot queries a minute would only compete with the live refresh.
setInterval(() => { if (!dashView.classList.contains('hidden')) refreshOverview(); }, 60000);

await initMap();
