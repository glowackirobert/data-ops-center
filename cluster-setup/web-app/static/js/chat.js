// The /api/ask panel — an English question in, index-aware Pinot SQL and its
// answer out (see AI_PLATFORM_PLAN.md). The latency badge it renders reuses
// app.js's delegated explain-popover listener.

import { esc, postJson, latencyBadgeHtml } from './utils.js';
import { initPanel } from './panel.js';

export function initChat() {
  const result = document.getElementById('ask-result');
  initPanel({
    btn: document.getElementById('ask-btn'),
    box: document.getElementById('ask-box'),
    form: document.getElementById('ask-form'),
    input: document.getElementById('ask-input'),
    result,
    clearBtn: document.getElementById('ask-clear-btn'),
    idleLabel: 'Ask',
    submit: (question, signal) => postJson('/api/ask', { question }, signal),
    showResult: r => {
      const rationale = r.rationale
        ? `<div class="ask-rationale">${esc(r.rationale)}${latencyBadgeHtml(r.ms, r.stats)}</div>`
        : '';
      const sql = r.sql ? `<pre class="ask-sql">${esc(r.sql)}</pre>` : '';
      result.innerHTML = `<div class="ask-answer">${esc(r.answer)}</div>${rationale}${sql}`;
    },
    showError: err => {
      result.innerHTML = `<div class="ask-error">${esc(err.message)}</div>`;
    },
  });
}
