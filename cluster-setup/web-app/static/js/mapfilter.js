// The Filter box — a plain-English sentence turned into a map filter (see
// AI_PLATFORM_PLAN.md Track 2). Owns its own panel open/close/submit UI,
// the same pattern as chat.js's Ask panel; the actual filter application
// (which touches the map, the route dropdown and the heatmap toggle) is
// map.js's job, handed in as `onApply` so this module needs no access to
// the map itself.

import { postJson, describeMapFilter } from './utils.js';
import { els } from './state.js';

export function initMapFilter(onApply) {
  const btn = els.mapFilterBtn;
  const box = els.mapFilterBox;
  const form = els.mapFilterForm;
  const input = els.mapFilterInput;
  const result = els.mapFilterResult;

  let busy = false;

  const open = () => {
    box.classList.remove('hidden');
    btn.classList.add('active');
    input.focus();
  };
  const close = () => {
    box.classList.add('hidden');
    btn.classList.remove('active');
  };

  btn.onclick = () => (box.classList.contains('hidden') ? open() : close());
  box.querySelector('.close').onclick = close;

  form.onsubmit = async e => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text || busy) return;
    busy = true;
    result.textContent = 'Thinking…';
    try {
      const filter = await postJson('/api/map-filter', { text });
      onApply(filter);
      result.textContent = describeMapFilter(filter);
    } catch (err) {
      result.textContent = `Filter failed: ${err.message}`;
    } finally {
      busy = false;
    }
  };
}
