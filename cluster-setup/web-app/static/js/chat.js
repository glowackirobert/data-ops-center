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
    const question = input.value.trim();
    if (!question || busy) return;
    busy = true;
    result.innerHTML = '<div class="ask-pending">Thinking…</div>';
    try {
      renderResult(result, await postJson('/api/ask', { question }));
    } catch (err) {
      renderError(result, err);
    } finally {
      busy = false;
    }
  };
}
