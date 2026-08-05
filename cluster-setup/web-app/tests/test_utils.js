// Tests for static/js/utils.js — pure formatting helpers, no DOM dependency,
// so this runs directly under Node's built-in test runner: node --test tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { latencyMs, latencyBadgeHtml, elapsedClock } from '../static/js/utils.js';

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

test('elapsedClock: placeholder when no timestamp yet', () => {
  assert.equal(elapsedClock(0), '--:--:--.---');
  assert.equal(elapsedClock(null), '--:--:--.---');
});

test('elapsedClock: formats hh:mm:ss.sss for a known offset', () => {
  const oneHourFiveMinTenSec333ms = 3600000 + 5 * 60000 + 10000 + 333;
  const since = Date.now() - oneHourFiveMinTenSec333ms;
  // tolerate the few ms Date.now() drifts between building `since` and the call
  assert.match(elapsedClock(since), /^01:05:1[01]\.\d{3}$/);
});

test('elapsedClock: never goes negative for a future timestamp', () => {
  assert.equal(elapsedClock(Date.now() + 5000), '00:00:00.000');
});
