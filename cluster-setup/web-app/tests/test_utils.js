// Tests for static/js/utils.js — pure formatting helpers, no DOM dependency,
// so this runs directly under Node's built-in test runner: node --test tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { latencyMs, latencyBadgeHtml, toDisplayedMinute, displayedShiftMin, minutesUntil }
  from '../static/js/utils.js';

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

// Departure rows: the chip and the struck-through schedule must be the same
// decision. GTFS departure times sit on whole minutes, so these cases are the
// ones that used to disagree — see the comment on displayedShiftMin.
const at = (h, m, s = 0) => Date.UTC(2026, 7, 21, h, m, s);

test('displayedShiftMin: on time is a single value, no chip', () => {
  assert.equal(displayedShiftMin(at(22, 18), at(22, 18)), 0);
});

test('displayedShiftMin: early by under half a minute stays quiet', () => {
  // Used to render "22:17" struck through against "22:18" with no chip.
  for (const early of [1, 15, 29]) {
    assert.equal(displayedShiftMin(at(22, 18), at(22, 17, 60 - early)), 0);
  }
});

test('displayedShiftMin: late by under half a minute stays quiet', () => {
  for (const late of [1, 15, 29]) {
    assert.equal(displayedShiftMin(at(22, 18), at(22, 18, late)), 0);
  }
});

test('displayedShiftMin: lateness that moves the shown minute earns its chip', () => {
  // Used to render a "+1" chip beside two identical times: the chip rounded
  // 40 s up to a minute while the printed time truncated it away. Now the
  // printed time rounds too, so the row reads 22:19 against a struck 22:18.
  for (const late of [30, 40, 59]) {
    assert.equal(displayedShiftMin(at(22, 18), at(22, 18, late)), 1);
  }
});

test('displayedShiftMin: a visible minute of earliness reports -1', () => {
  assert.equal(displayedShiftMin(at(22, 18), at(22, 17, 10)), -1);
});

test('displayedShiftMin: a visible minute of lateness reports +1', () => {
  assert.equal(displayedShiftMin(at(22, 3), at(22, 4, 5)), 1);
});

test('displayedShiftMin: scales past a single minute', () => {
  assert.equal(displayedShiftMin(at(22, 18), at(22, 25)), 7);
  assert.equal(displayedShiftMin(at(22, 18), at(22, 11)), -7);
});

test('toDisplayedMinute: rounds to the minute the row shows', () => {
  assert.equal(toDisplayedMinute(at(22, 17, 45)), at(22, 18));
  assert.equal(toDisplayedMinute(at(22, 17, 10)), at(22, 17));
});

test('displayedShiftMin: shift is exactly the gap between the shown times', () => {
  // The invariant the popup relies on: chip value == difference of the two
  // times printed in the row, so a reader can never see one contradict the other.
  const cases = [[at(22, 18), at(22, 17, 10)], [at(22, 3), at(22, 4, 5)],
                 [at(22, 18), at(22, 18, 40)], [at(23, 59), at(0, 1)]];
  for (const [sched, eta] of cases) {
    const shift = displayedShiftMin(sched, eta);
    assert.equal(toDisplayedMinute(eta) - toDisplayedMinute(sched), shift * 60000);
  }
});

test('minutesUntil: equals printed departure minute minus the clock minute', () => {
  // The whole point: a reader subtracting the two numbers the row prints must
  // land on the countdown it prints. Rounding the raw gap failed this in half
  // of all cases — 20:57 shown at 20:55:35 printed "1 min", not 2.
  assert.equal(minutesUntil(at(20, 57), at(20, 55, 35)), 2);
  assert.equal(minutesUntil(at(20, 57), at(20, 55, 59)), 2);
  assert.equal(minutesUntil(at(20, 57), at(20, 56, 1)), 1);
});

test('minutesUntil: the departing minute reads as 0', () => {
  assert.equal(minutesUntil(at(20, 57), at(20, 57, 30)), 0);
});

test('minutesUntil: never negative for a row still on screen', () => {
  assert.equal(minutesUntil(at(20, 57), at(20, 58, 10)), 0);
});

test('minutesUntil: holds across the whole popup window', () => {
  for (let m = 0; m < 60; m++) {
    for (const sec of [0, 17, 35, 59]) {
      const now = at(20, 0, sec);
      const shown = at(20 + Math.floor(m / 60), m % 60);
      const printed = minutesUntil(shown, now);
      assert.equal(printed, Math.max(0, (shown - toDisplayedMinute(at(20, 0))) / 60000),
        `m=${m} sec=${sec}`);
    }
  }
});
