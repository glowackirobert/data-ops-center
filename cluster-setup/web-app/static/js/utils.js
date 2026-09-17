// Shared helpers with no DOM, map, or app-state dependency — formatting,
// route naming, small HTML fragments, and the one fetch wrapper.

const PALETTE = [
  [31,119,180],[255,127,14],[44,160,44],[214,39,40],[148,103,189],
  [140,86,75],[227,119,194],[127,127,127],[188,189,34],[23,190,207],
  [0,122,135],[255,20,147],[70,130,180],[210,105,30],[60,179,113],
];

export function colorForRoute(route) {
  let h = 0;
  for (const ch of String(route || '?')) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

// 24-hour clock regardless of the browser's locale (h23 avoids the "24:xx"
// midnight quirk of hour12: false).
export function time24(ms) {
  return new Date(ms).toLocaleTimeString([], { hourCycle: 'h23' });
}

export function timeHM(ms) {
  return new Date(ms).toLocaleTimeString(
    [], { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
}

// A departure row shows two facts that must never disagree: the expected time
// leading the row, and — when a live delay moved it — the scheduled time struck
// through beside it with a +N/-N chip. They disagreed because they came from
// two different numbers: the chip from the raw delay rounded to whole minutes
// (0 for anything under 30 s), the strike-through from the *displayed* minute,
// which truncates. GTFS departure times sit on whole minutes, so a tram one
// second early already renders a minute earlier — and printed "22:17 22:18"
// with nothing saying why. The mirror case, late by 30-59 s, printed a "+1"
// chip beside two identical times.
//
// So both now come from one number: the gap between the two times once each is
// rounded to the minute it is displayed as. Zero means the row shows a single
// value and no chip; non-zero means it shows both, and the chip is exactly the
// gap the reader can see between them.
export function toDisplayedMinute(ms) {
  return Math.round(ms / 60000) * 60000;
}

export function displayedShiftMin(scheduledMs, estimatedMs) {
  return (toDisplayedMinute(estimatedMs) - toDisplayedMinute(scheduledMs)) / 60000;
}

// The countdown is the third number in a departure row and has to survive the
// same test: printed departure time minus the reader's clock must equal it.
// Rounding the raw millisecond gap fails that half the time, because a clock
// truncates and this rounded — at 20:55:35 a departure shown as 20:57 printed
// "1 min" while the two printed numbers subtract to 2. Anchoring both ends to
// the minute each is *displayed* as makes the row read consistently, at the
// cost of calling a bus 20 s away "1 min" instead of "0".
export function minutesUntil(displayedMs, nowMs) {
  return Math.max(0, (displayedMs - Math.floor(nowMs / 60000) * 60000) / 60000);
}

export function esc(s) {
  return String(s ?? '').replace(/[&<>"]/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

// Gdansk tram routes are 1-2 digits; 3-digit and N-prefixed routes are buses
// (same heuristic as the dashboard queries).
export function isTram(route) {
  return /^\d{1,2}$/.test(String(route || ''));
}

// Gdansk night bus routes are N-prefixed (e.g. N1, N4, N8).
export function isNightBus(route) {
  return /^N/i.test(String(route || ''));
}

export function fmtBytes(b) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
  return b.toFixed(i && b < 10 ? 1 : 0) + ' ' + units[i];
}

// Pinot's own broker-reported query time (see app/http.py) travels as a
// response header rather than in the JSON body, so no existing consumer's
// parsing has to change. Returns null when the endpoint didn't set it.
export function latencyMs(resp) {
  const v = resp.headers.get('X-Pinot-Time-Ms');
  return v === null ? null : Number(v);
}

// The scan figures behind that number (docsScanned/totalDocs/segments…),
// same header-not-body reasoning as latencyMs. Compact JSON (see
// app/http.py's send()) — Number() has nothing to parse here, so this just
// unwraps the header and returns null for a missing or malformed one rather
// than throwing and taking the whole render down with it.
export function statsFromResponse(resp) {
  const v = resp.headers.get('X-Pinot-Stats');
  if (v === null) return null;
  try {
    return JSON.parse(v);
  } catch {
    return null;
  }
}

// The one-line reading that makes the scan figures land: the ratio, not the
// raw counts. More decimals
// as the ratio shrinks, since "0%" and "0.009%" are very different claims
// about a columnar store. totalDocs of 0 (or no stats at all) has no ratio
// to report.
function explainDecimals(pct) {
  if (pct < 0.01) return 3;
  if (pct < 1) return 2;
  if (pct < 10) return 1;
  return 0;
}

export function explainReading(stats) {
  if (!stats?.totalDocs) return null;
  const pct = (stats.docsScanned / stats.totalDocs) * 100;
  return `${pct.toFixed(explainDecimals(pct))}% of the table`;
}

export function latencyBadgeHtml(ms, stats) {
  if (ms == null) return '';
  const attr = stats ? ` data-stats="${esc(JSON.stringify(stats))}"` : '';
  return ` <span class="latency-badge"${attr}>${ms} ms</span>`;
}

// The route a position row is on, as the dropdown and the badges spell it.
// One expression rather than six copies of the same `|| '?'` fallback, which
// has to agree everywhere or a vehicle with no route silently stops matching
// the filter that is drawing it.
export function routeOf(d) {
  return String(d.route || '?');
}

// Every endpoint in this app answers JSON and, when Pinot backed it, an
// X-Pinot-Time-Ms header — so every caller was writing the same four lines.
// Errors carry the server's own `error` text when it sent one (the Pinot
// message beats a bare "HTTP 502") and the status code, which /api/route-shape
// needs to tell "no shape today" apart from a real failure.
export async function getJson(url) {
  const resp = await fetch(url);
  if (!resp.ok) {
    // Only an *error* body may fail to parse — a 404 from the static handler
    // is plain text — and failing to parse it must not mask the status.
    const data = await resp.json().catch(() => null);
    const detail = typeof data?.error === 'string' ? data.error : null;
    const err = new Error(detail || `HTTP ${resp.status}`);
    err.status = resp.status;
    throw err;
  }
  // A 200 whose body does not parse is a failure, so let it reject: callers
  // assign the result straight into state, and handing them `null` there put
  // `null` in state.lastRows on a connection reset mid-body — the next render
  // then died on "Cannot read properties of null (reading 'filter')" instead
  // of keeping the rows already on screen.
  return { data: await resp.json(), ms: latencyMs(resp), stats: statsFromResponse(resp) };
}

// POST counterpart to getJson, for the one endpoint (/api/ask) that takes a
// body. Same error-unwrapping rule: the server's own `error` text beats a
// bare status code.
export async function postJson(url, body) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => null);
    const detail = typeof data?.error === 'string' ? data.error : null;
    const err = new Error(detail || `HTTP ${resp.status}`);
    err.status = resp.status;
    throw err;
  }
  return resp.json();
}

// Both hour-of-day charts (route delay on the map, network rush hour on the
// Analytics tab) are the same bar strip against the same CSS, differing only
// in what a bar means — so they share the drawing and pass their own titles.
// Bars are scaled from a zero baseline, not from the smallest value: average
// delay goes negative (a line running early) and a bar chart that hides the
// sign is worse than no chart. `null` is "no data for this hour" and draws an
// empty slot, so a night line still reads as a full day.
export function hourlyBarsHtml(values, title) {
  const known = values.filter(v => v != null);
  const min = Math.min(0, ...known);
  const max = known.length ? Math.max(0, ...known) : 1;
  const range = (max - min) || 1;
  return values.map((v, hour) => {
    const pct = v == null ? 0 : Math.max(Math.round(((v - min) / range) * 100), 2);
    return `<div class="bar" style="height:${pct}%" title="${title(v, hour)}"></div>`;
  }).join('');
}

// The 'HH:00' keys both hour-of-day endpoints label their rows with
// (see _hour_labeled in app/pinot.py).
export function hourKey(h) {
  return `${String(h).padStart(2, '0')}:00`;
}

export const HOUR_TICKS_HTML =
  [0, 6, 12, 18].map(h => `<span>${h}:00</span>`).join('');
