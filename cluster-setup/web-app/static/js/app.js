import { initMap, resizeMap } from './map.js';
import { initDashboard, refreshOverview } from './dashboard.js';
import { initHelp } from './help.js';
import { explainReading } from './utils.js';

function showTab(name) {
  const isMap = name === 'map';
  document.getElementById('map-view').classList.toggle('hidden', !isMap);
  document.getElementById('dash-view').classList.toggle('hidden', isMap);
  document.getElementById('tab-map').classList.toggle('active', isMap);
  document.getElementById('tab-dash').classList.toggle('active', !isMap);
  if (isMap) resizeMap(); // container was display:none while hidden
  if (!isMap) initDashboard();
}

document.getElementById('tab-map').onclick = () => showTab('map');
document.getElementById('tab-dash').onclick = () => showTab('dash');

// Explain panel: every latency badge that carries scan stats (see
// latencyBadgeHtml in utils.js) opens a small popover of the figures behind
// its number on click. One delegated listener rather than one per badge —
// badges are rebuilt into fresh DOM nodes on every panel refresh (map status
// line every 10 s, stopbox on every poll), so per-node listeners would just
// be discarded along with the old nodes.
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

refreshOverview(); // also feeds the storage strip from its /api/stats fetch
setInterval(refreshOverview, 60000);

await initMap();
