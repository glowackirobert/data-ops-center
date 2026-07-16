import { initMap, resizeMap } from './map.js';
import { initDashboard, refreshStats } from './dashboard.js';

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

refreshStats();
setInterval(refreshStats, 60000);

await initMap();
