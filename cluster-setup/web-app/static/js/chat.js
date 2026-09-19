// The /api/ask panel — an English question in, index-aware Pinot SQL and its
// answer out (see AI_PLATFORM_PLAN.md). Self-contained: its own toggle
// button, its own panel, and the one endpoint it talks to. The latency badge
// it renders reuses app.js's delegated explain-popover listener, so opening
// the scan-stats popup needs no wiring here.

import { esc, postJson, latencyBadgeHtml } from './utils.js';

function renderResult(container, result) {
  const rationale = result.rationale
    ? `<div class="ask-rationale">${esc(result.rationale)}${latencyBadgeHtml(result.ms, result.stats)}</div>`
    : '';
  const sql = result.sql ? `<pre class="ask-sql">${esc(result.sql)}</pre>` : '';
  container.innerHTML =
    `<div class="ask-answer">${esc(result.answer)}</div>${rationale}${sql}`;
}

function renderError(container, err) {
  container.innerHTML = `<div class="ask-error">${esc(err.message)}</div>`;
}

export function initChat() {
  const btn = document.getElementById('ask-btn');
  const box = document.getElementById('ask-box');
  const form = document.getElementById('ask-form');
  const input = document.getElementById('ask-input');
  const result = document.getElementById('ask-result');
  const submitBtn = form.querySelector('button[type="submit"]');
  const clearBtn = document.getElementById('ask-clear-btn');

  let controller = null; // non-null exactly while a request is in flight

  const setBusy = busy => {
    input.disabled = busy;
    submitBtn.textContent = busy ? 'Stop' : 'Ask';
  };

  // Cancels whatever's in flight, if anything, and drops the "Thinking…"
  // placeholder it left up — the box stays open after a Stop click, so a
  // stale "Thinking…" with the input re-enabled would look like it's still
  // working. Used by Stop, Clear, and close() — closing the box mid-question
  // must not let a stale answer land and repopulate a panel the user isn't
  // even looking at any more.
  const stop = () => {
    if (!controller) return;
    controller.abort();
    controller = null;
    setBusy(false);
    result.innerHTML = '';
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
    const question = input.value.trim();
    if (!question) return;
    controller = new AbortController();
    setBusy(true);
    result.innerHTML = '<div class="ask-pending">Thinking…</div>';
    try {
      renderResult(result, await postJson('/api/ask', { question }, controller.signal));
    } catch (err) {
      if (err.name !== 'AbortError') renderError(result, err);
    } finally {
      controller = null;
      setBusy(false);
    }
  };
}
