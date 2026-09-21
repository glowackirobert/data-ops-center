// Shared helpers with no DOM, map, or app-state dependency — formatting,
// route naming, small HTML fragments, and the fetch wrappers.

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

// A departure row's expected time, struck-through schedule and +N/-N chip
// must agree, so all three come from one number: the gap between the two
// times once each is rounded to the minute it is displayed as. Zero means a
// single value and no chip. (GTFS times sit on whole minutes, so a tram one
// second early would otherwise print a minute earlier with no chip.)
export function toDisplayedMinute(ms) {
  return Math.round(ms / 60000) * 60000;
}

export function displayedShiftMin(scheduledMs, estimatedMs) {
  return (toDisplayedMinute(estimatedMs) - toDisplayedMinute(scheduledMs)) / 60000;
}

// Anchored to displayed minutes at both ends so the countdown equals the
// printed departure time minus the reader's clock minute.
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

export function isNightBus(route) {
  return /^N/i.test(String(route || ''));
}

export function fmtBytes(b) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
  return b.toFixed(i && b < 10 ? 1 : 0) + ' ' + units[i];
}

// Pinot's broker-reported query time and the scan figures behind it travel
// as response headers (see app/http.py), not in the JSON body. Both are
// null when the endpoint didn't set them.
export function latencyMs(resp) {
  const v = resp.headers.get('X-Pinot-Time-Ms');
  return v === null ? null : Number(v);
}

export function statsFromResponse(resp) {
  const v = resp.headers.get('X-Pinot-Stats');
  if (v === null) return null;
  try {
    return JSON.parse(v);
  } catch {
    return null;
  }
}

// More decimals as the ratio shrinks: "0%" and "0.009%" are very different
// claims about a columnar store.
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

// The route a position row is on, as the dropdown and the badges spell it —
// one fallback for a missing route, or the filter and the badge disagree.
export function routeOf(d) {
  return String(d.route || '?');
}

// The server's own `error` text beats a bare status; `status` lets
// /api/route-shape tell "no shape today" (404) from a real failure.
async function throwHttpError(resp) {
  // Only an error body may fail to parse (a 404 from the static handler is
  // plain text), and that must not mask the status.
  const data = await resp.json().catch(() => null);
  const detail = typeof data?.error === 'string' ? data.error : null;
  const err = new Error(detail || `HTTP ${resp.status}`);
  err.status = resp.status;
  throw err;
}

export async function getJson(url) {
  const resp = await fetch(url);
  if (!resp.ok) await throwHttpError(resp);
  // A 200 whose body doesn't parse rejects rather than yielding null —
  // callers assign the result straight into state.
  return { data: await resp.json(), ms: latencyMs(resp), stats: statsFromResponse(resp) };
}

// `signal` lets a Stop click cancel a request in flight; fetch then rejects
// with an AbortError the caller treats as silent.
export async function postJson(url, body, signal) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok) await throwHttpError(resp);
  return resp.json();
}

// Readback of a /api/map-filter response built from its structured fields,
// so it can never claim something the filter didn't actually apply.
export function describeMapFilter(filter) {
  const parts = [];
  // Only the first route: applyMapFilter sets a single-select dropdown.
  if (filter.routes?.length) parts.push(`route ${filter.routes[0]}`);
  if (filter.minDelaySec != null) {
    parts.push(`delayed ${Math.round(filter.minDelaySec / 60)}+ min`);
  }
  if (filter.inServiceOnly) parts.push('in service only');
  if (filter.placeMatch) parts.push(`near ${filter.placeMatch.name}`);
  if (filter.heatmap) parts.push('heatmap');
  return parts.length ? `Showing: ${parts.join(', ')}` : 'No filter recognized — showing everything';
}

// Shared by both hour-of-day charts. Bars scale from a zero baseline, since
// average delay goes negative; `null` is "no data" and draws an empty slot.
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
