// Tests for static/js/utils.js — pure formatting helpers, no DOM dependency,
// so this runs directly under Node's built-in test runner: node --test tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { latencyMs, latencyBadgeHtml } from '../static/js/utils.js';

function fakeResponse(headerValue) {
  return { headers: { get: name => name === 'X-Pinot-Time-Ms' ? headerValue : null } };
}

test('latencyMs: reads and numifies the header', () => {
  assert.equal(latencyMs(fakeResponse('42')), 42);
});

test('latencyMs: null when the header is absent', () => {
  assert.equal(latencyMs(fakeResponse(null)), null);
});

test('latencyBadgeHtml: empty string for null', () => {
  assert.equal(latencyBadgeHtml(null), '');
});

test('latencyBadgeHtml: renders the value inside a badge span', () => {
  assert.match(latencyBadgeHtml(7), /class="latency-badge"/);
  assert.match(latencyBadgeHtml(7), /7 ms/);
});
