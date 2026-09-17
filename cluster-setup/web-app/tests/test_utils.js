// Tests for static/js/utils.js — pure formatting helpers, no DOM dependency,
// so this runs directly under Node's built-in test runner: node --test tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { latencyMs, statsFromResponse, explainReading, latencyBadgeHtml,
         toDisplayedMinute, displayedShiftMin, minutesUntil,
         routeOf, getJson, postJson, describeMapFilter, hourlyBarsHtml,
         HOUR_TICKS_HTML }
  from '../static/js/utils.js';

function fakeResponse(headers) {
  return { headers: { get: name => (name in headers ? headers[name] : null) } };
}

test('latencyMs: reads and numifies the header', () => {
  assert.equal(latencyMs(fakeResponse({ 'X-Pinot-Time-Ms': '42' })), 42);
});

test('latencyMs: null when the header is absent', () => {
  assert.equal(latencyMs(fakeResponse({})), null);
});

// statsFromResponse - the explain panel's raw material (see app/http.py's
// X-Pinot-Stats header).

test('statsFromResponse: parses the compact JSON header', () => {
  const stats = { docsScanned: 5279, totalDocs: 60417459, segmentsProcessed: 32,
                  segmentsQueried: 33, segmentsPruned: 1, serversQueried: 2 };
  const resp = fakeResponse({ 'X-Pinot-Stats': JSON.stringify(stats) });
  assert.deepEqual(statsFromResponse(resp), stats);
});

test('statsFromResponse: null when the header is absent', () => {
  assert.equal(statsFromResponse(fakeResponse({})), null);
});

test('statsFromResponse: null rather than throwing on a malformed header', () => {
  assert.equal(statsFromResponse(fakeResponse({ 'X-Pinot-Stats': 'not json' })), null);
});

// explainReading - the ratio, not the raw counts, is the number that lands.

test('explainReading: rounds a tiny ratio to three decimals', () => {
  assert.equal(
    explainReading({ docsScanned: 5279, totalDocs: 60417459 }), '0.009% of the table');
});

test('explainReading: fewer decimals as the ratio grows', () => {
  assert.equal(explainReading({ docsScanned: 500, totalDocs: 1000 }), '50% of the table');
  assert.equal(explainReading({ docsScanned: 50, totalDocs: 1000 }), '5.0% of the table');
  assert.equal(explainReading({ docsScanned: 5, totalDocs: 1000 }), '0.50% of the table');
});

test('explainReading: null when there are no stats at all', () => {
  assert.equal(explainReading(null), null);
});

test('explainReading: null when totalDocs is 0 rather than dividing by it', () => {
  assert.equal(explainReading({ docsScanned: 0, totalDocs: 0 }), null);
});

// latencyBadgeHtml - stays a plain badge at rest; carries the stats along as
// a data attribute for app.js's click-to-open popover when they're known.

test('latencyBadgeHtml: empty string for null', () => {
  assert.equal(latencyBadgeHtml(null), '');
});

test('latencyBadgeHtml: renders the value inside a badge span', () => {
  assert.match(latencyBadgeHtml(7), /class="latency-badge"/);
  assert.match(latencyBadgeHtml(7), /7 ms/);
});

test('latencyBadgeHtml: no data-stats attribute when stats are unknown', () => {
  assert.doesNotMatch(latencyBadgeHtml(7), /data-stats/);
});

test('latencyBadgeHtml: carries the stats as an escaped data attribute', () => {
  const html = latencyBadgeHtml(7, { docsScanned: 5279, totalDocs: 60417459 });
  const m = /data-stats="([^"]*)"/.exec(html);
  assert.ok(m, 'expected a data-stats attribute');
  const decoded = m[1].replaceAll('&quot;', '"');
  assert.deepEqual(JSON.parse(decoded), { docsScanned: 5279, totalDocs: 60417459 });
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

// routeOf - the one spelling of "which line is this row on", shared by the
// filter, the badges and the camera fit. They have to agree.

test('routeOf: stringifies whatever the feed sent', () => {
  assert.equal(routeOf({ route: 8 }), '8');
  assert.equal(routeOf({ route: 'N1' }), 'N1');
});

test('routeOf: a missing route is the same placeholder everywhere', () => {
  // '?' is what the badge draws, so it must also be what the filter matches -
  // otherwise a vehicle is drawn that its own line filter cannot select.
  assert.equal(routeOf({}), '?');
  assert.equal(routeOf({ route: '' }), '?');
  assert.equal(routeOf({ route: null }), '?');
});

// getJson - the one fetch wrapper. Every endpoint answers JSON plus, when
// Pinot backed it, the latency header.

function stubFetch(impl) {
  globalThis.fetch = impl;
}

test('getJson: returns the parsed body, the Pinot latency and its scan stats', async () => {
  const stats = { docsScanned: 5279, totalDocs: 60417459 };
  stubFetch(async () => ({
    ok: true, status: 200,
    json: async () => ({ rows: 3 }),
    ...fakeResponse({ 'X-Pinot-Time-Ms': '17', 'X-Pinot-Stats': JSON.stringify(stats) }),
  }));
  assert.deepEqual(
    await getJson('/api/whatever'), { data: { rows: 3 }, ms: 17, stats });
});

test('getJson: ms is null for an endpoint that queried no Pinot', async () => {
  stubFetch(async () => ({
    ok: true, status: 200,
    json: async () => ([]),
    headers: { get: () => null },
  }));
  const { ms } = await getJson('/api/stops');
  assert.equal(ms, null);
});

test('getJson: an error body beats the bare status code', async () => {
  // A 502 from Pinot carries the broker's own message; surfacing "HTTP 502"
  // instead would throw away the only useful half of the response.
  stubFetch(async () => ({
    ok: false, status: 502,
    json: async () => ({ error: 'no such column: bogus' }),
    headers: { get: () => null },
  }));
  await assert.rejects(getJson('/api/positions'), /no such column: bogus/);
});

test('getJson: a 200 whose body will not parse rejects, it does not yield null', async () => {
  // A connection reset mid-body does exactly this. Returning { data: null }
  // put null into state.lastRows and the next render died on it; rejecting
  // leaves the caller's catch to keep the rows already on screen.
  stubFetch(async () => ({
    ok: true, status: 200,
    json: async () => { throw new TypeError('network error'); },
    headers: { get: () => null },
  }));
  await assert.rejects(getJson('/api/positions'), /network error/);
});

test('getJson: falls back to the status when the body has no message', async () => {
  stubFetch(async () => ({
    ok: false, status: 500,
    json: async () => { throw new SyntaxError('not json'); },
    headers: { get: () => null },
  }));
  await assert.rejects(getJson('/x'), /HTTP 500/);
});

test('getJson: the thrown error carries the status', async () => {
  // /api/route-shape 404s for a vehicle between trips, which the map reports
  // as a fact rather than a failure - it needs to tell the two apart.
  stubFetch(async () => ({
    ok: false, status: 404,
    json: async () => ({ error: 'no shape for this trip today' }),
    headers: { get: () => null },
  }));
  await assert.rejects(getJson('/api/route-shape'), err => err.status === 404);
});

// postJson - the /api/ask fetch wrapper. Same error-unwrapping rule as
// getJson, checked once here rather than duplicated in full; the body-shape
// cases above already cover it.

test('postJson: sends the body as JSON and returns the parsed response', async () => {
  let sentUrl, sentInit;
  stubFetch(async (url, init) => {
    sentUrl = url; sentInit = init;
    return { ok: true, status: 200, json: async () => ({ answer: 'Route 8 has 3 vehicles.' }) };
  });
  const result = await postJson('/api/ask', { question: 'how many on route 8?' });
  assert.deepEqual(result, { answer: 'Route 8 has 3 vehicles.' });
  assert.equal(sentUrl, '/api/ask');
  assert.equal(sentInit.method, 'POST');
  assert.equal(sentInit.headers['Content-Type'], 'application/json');
  assert.deepEqual(JSON.parse(sentInit.body), { question: 'how many on route 8?' });
});

test('postJson: an error body beats the bare status code', async () => {
  stubFetch(async () => ({
    ok: false, status: 429,
    json: async () => ({ error: 'rate limit exceeded, try again shortly' }),
  }));
  await assert.rejects(postJson('/api/ask', { question: 'x' }), /rate limit exceeded/);
});

// describeMapFilter - the Filter box's plain-English readback.

test('describeMapFilter: nothing recognized says so', () => {
  assert.equal(
    describeMapFilter({ routes: [], minDelaySec: null, inServiceOnly: false,
                        heatmap: false, placeMatch: null }),
    'No filter recognized — showing everything');
});

test('describeMapFilter: a single route', () => {
  assert.equal(
    describeMapFilter({ routes: ['8'], minDelaySec: null, inServiceOnly: false,
                        heatmap: false, placeMatch: null }),
    'Showing: route 8');
});

test('describeMapFilter: multiple routes pluralize', () => {
  assert.equal(
    describeMapFilter({ routes: ['8', '12'], minDelaySec: null, inServiceOnly: false,
                        heatmap: false, placeMatch: null }),
    'Showing: routes 8, 12');
});

test('describeMapFilter: a delay threshold reads in minutes', () => {
  assert.equal(
    describeMapFilter({ routes: [], minDelaySec: 300, inServiceOnly: false,
                        heatmap: false, placeMatch: null }),
    'Showing: delayed 5+ min');
});

test('describeMapFilter: a place match names the place', () => {
  assert.equal(
    describeMapFilter({ routes: [], minDelaySec: null, inServiceOnly: false,
                        heatmap: false, placeMatch: { name: 'Gdańsk Wrzeszcz PKP' } }),
    'Showing: near Gdańsk Wrzeszcz PKP');
});

test('describeMapFilter: every field combines into one line', () => {
  assert.equal(
    describeMapFilter({
      routes: ['8'], minDelaySec: 300, inServiceOnly: true,
      heatmap: true, placeMatch: { name: 'Wrzeszcz' },
    }),
    'Showing: route 8, delayed 5+ min, in service only, near Wrzeszcz, heatmap');
});

// hourlyBarsHtml - shared by the route-delay histogram and the network
// rush-hour strip, which differ only in what a bar means.

test('hourlyBarsHtml: one bar per slot, titled by the caller', () => {
  const html = hourlyBarsHtml([1, 2], (v, h) => `${h}h=${v}`);
  assert.equal(html.match(/<div class="bar"/g).length, 2);
  assert.match(html, /title="0h=1"/);
  assert.match(html, /title="1h=2"/);
});

test('hourlyBarsHtml: the tallest bar is full height', () => {
  assert.match(hourlyBarsHtml([5, 10], () => ''), /height:100%/);
});

test('hourlyBarsHtml: null draws an empty slot, not a gap in the axis', () => {
  const html = hourlyBarsHtml([null, 10], () => '');
  assert.equal(html.match(/<div class="bar"/g).length, 2);
  assert.match(html, /height:0%/);
});

function heights(html) {
  return [...html.matchAll(/height:(\d+)%/g)].map(m => Number(m[1]));
}

test('hourlyBarsHtml: the baseline is zero, not the smallest value', () => {
  // Scaling from the smallest value exaggerates: two hours 10 s apart would
  // draw as an empty bar beside a full one.
  assert.deepEqual(heights(hourlyBarsHtml([100, 110], () => '')), [91, 100]);
});

test('hourlyBarsHtml: a negative average delay reads as below the zero line', () => {
  // A line running early. The zero baseline puts 0 s halfway up when the
  // day swings equally either side of it, so the sign stays visible.
  assert.deepEqual(heights(hourlyBarsHtml([-60, 0, 60], () => '')), [2, 50, 100]);
});

test('hourlyBarsHtml: a present-but-zero value still draws a visible stub', () => {
  // 2% minimum: an hour with data reads differently from an hour without.
  assert.match(hourlyBarsHtml([0, 0], () => ''), /height:2%/);
});

test('hourlyBarsHtml: no data at all does not divide by zero', () => {
  assert.equal(hourlyBarsHtml([], () => ''), '');
  assert.match(hourlyBarsHtml([null], () => ''), /height:0%/);
});

test('HOUR_TICKS_HTML: quarter-day labels under both charts', () => {
  assert.equal(HOUR_TICKS_HTML,
    '<span>0:00</span><span>6:00</span><span>12:00</span><span>18:00</span>');
});
