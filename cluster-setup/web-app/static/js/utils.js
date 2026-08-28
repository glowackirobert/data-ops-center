// Shared formatting helpers with no DOM/map/state dependency.

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

export function latencyBadgeHtml(ms) {
  return ms == null ? '' : ` <span class="latency-badge">${ms} ms</span>`;
}
