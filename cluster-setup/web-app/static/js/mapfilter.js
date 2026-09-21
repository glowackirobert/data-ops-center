// The Filter box — a plain-English sentence turned into a map filter (see
// AI_PLATFORM_PLAN.md Track 2). Applying the filter touches the map, the
// route dropdown and the heatmap toggle, so that is map.js's job, handed in
// as `onApply`. Returns panel.js's `clearResult` so map.js can clear the
// readback when a manual dropdown pick drops the filter.

import { postJson, describeMapFilter } from './utils.js';
import { els } from './state.js';
import { initPanel } from './panel.js';

export function initMapFilter(onApply) {
  const result = els.mapFilterResult;
  return initPanel({
    btn: els.mapFilterBtn,
    box: els.mapFilterBox,
    form: els.mapFilterForm,
    input: els.mapFilterInput,
    result,
    clearBtn: els.mapFilterClearBtn,
    idleLabel: 'Go',
    submit: (text, signal) => postJson('/api/map-filter', { text }, signal),
    showResult: filter => {
      onApply(filter);
      result.textContent = describeMapFilter(filter);
    },
    showError: err => {
      result.textContent = `Filter failed: ${err.message}`;
    },
  });
}
