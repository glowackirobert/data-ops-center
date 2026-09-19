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
  const submitBtn = form.querySelector('button[type="submit"]');
  const clearBtn = els.mapFilterClearBtn;

  let controller = null; // non-null exactly while a request is in flight

  const setBusy = busy => {
    input.disabled = busy;
    submitBtn.textContent = busy ? 'Stop' : 'Go';
  };

  // Cancels whatever's in flight, if anything, and drops the "Thinking…"
  // placeholder it left up — the box stays open after a Stop click, so a
  // stale "Thinking…" with the input re-enabled would look like it's still
  // working. Used by Stop, Clear, and close() — closing the box mid-request
  // must not let a stale response land and repopulate a panel the user isn't
  // even looking at any more.
  const stop = () => {
    if (!controller) return;
    controller.abort();
    controller = null;
    setBusy(false);
    result.textContent = '';
  };

  const open = () => {
    box.classList.remove('hidden');
    btn.classList.add('active');
    input.focus();
  };
  const close = () => {
    stop();
    box.classList.add('hidden');
    btn.classList.remove('active');
  };
  const clear = () => {
    stop(); // also clears result
    input.value = '';
    input.focus();
  };

  btn.onclick = () => (box.classList.contains('hidden') ? open() : close());
  box.querySelector('.close').onclick = close;
  clearBtn.onclick = clear;

  form.onsubmit = async e => {
    e.preventDefault();
    if (controller) { stop(); return; } // submit button reads "Stop" while busy
    const text = input.value.trim();
    if (!text) return;
    controller = new AbortController();
    setBusy(true);
    result.textContent = 'Thinking…';
    try {
      const filter = await postJson('/api/map-filter', { text }, controller.signal);
      onApply(filter);
      result.textContent = describeMapFilter(filter);
    } catch (err) {
      if (err.name !== 'AbortError') result.textContent = `Filter failed: ${err.message}`;
    } finally {
      controller = null;
      setBusy(false);
    }
  };

  // Lets map.js's routeSelect.onchange keep this panel's readback honest: a
  // manual dropdown pick clears state.nlFilter (see map.js) but has no
  // other reason to reach into this module, so it calls this instead of
  // leaving "Showing: route 3, delayed 5+ min…" up after the map itself
  // has gone back to showing everything.
  return { clearResult: () => { result.textContent = ''; } };
}
