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

// hh:mm:ss.sss elapsed since sinceMs — a data-staleness clock meant to tick
// on its own timer, not just be recomputed when the underlying data changes.
export function elapsedClock(sinceMs) {
  if (!sinceMs) return '--:--:--.---';
  let ms = Math.max(0, Date.now() - sinceMs);
  const h = Math.floor(ms / 3600000); ms -= h * 3600000;
  const m = Math.floor(ms / 60000); ms -= m * 60000;
  const s = Math.floor(ms / 1000); ms -= s * 1000;
  const pad = (n, w = 2) => String(n).padStart(w, '0');
  return `${pad(h)}:${pad(m)}:${pad(s)}.${pad(ms, 3)}`;
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
