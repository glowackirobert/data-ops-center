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
