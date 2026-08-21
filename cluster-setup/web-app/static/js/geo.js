// Pure path-projection geometry: no DOM, deck.gl, or map-state dependency,
// so this can be unit-tested in isolation (see tests/test_geo.js). Mirrors
// app/geo.py's truncate_path_at on the server side (same equirectangular
// point-on-polyline projection, independently implemented for the browser).

// 30 m in squared degrees — candidates within this squared-distance margin
// of the best match are treated as tied.
export const AMBIGUITY_M2 = (30 / 111320) ** 2;

// Vehicles further than this from the trip shape are treated as off-route —
// typically standing at a depot beside the terminus.
export const OFF_ROUTE_M = 150;

// Fraction of the viewport, centred, in which a followed vehicle is left
// alone. Anything inside is "comfortably framed"; only a vehicle that drifts
// out earns a camera move.
export const FOLLOW_DEADZONE = 0.6;

// Should the camera recentre on a followed vehicle at this screen position?
// Without a deadzone the follow panTo's on every 10 s poll, so a bus that
// crept 8 m slides the whole map out from under whoever is reading the
// trajectory it drew. Both arguments are CSS pixels: `point` as returned by
// map.project(), `size` as the map container's clientWidth/clientHeight —
// never the canvas's width/height, which are device pixels and would scale
// the deadzone by devicePixelRatio on a HiDPI screen.
export function needsRecentre(point, size, deadzone = FOLLOW_DEADZONE) {
  const half = deadzone / 2;
  const outside = (v, extent) => !(extent > 0) || Math.abs(v / extent - 0.5) > half;
  return outside(point.x, size.width) || outside(point.y, size.height);
}

// Pixels between a click point and a popup anchored to it.
export const BOX_GAP_PX = 12;

// Place a popup near a click without letting it fall out of the map view.
// Measured size in, position out — the departures box runs from three rows to
// thirty, so a fixed size guess pushed most of a busy stop's list below the
// map. Preferred corner is after the click on both axes; when that overflows,
// flip to before it (which leaves the click point visible rather than covered),
// and clamp only when neither side fits.
export function placeBox(anchor, box, view, gap = BOX_GAP_PX) {
  const axis = (at, size, extent) => {
    if (at + gap + size <= extent) return at + gap;   // after the click
    if (at - gap - size >= 0) return at - gap - size; // flipped before it
    return Math.max(0, extent - size);                // neither side fits
  };
  return { left: axis(anchor.x, box.width, view.width),
           top: axis(anchor.y, box.height, view.height) };
}

// Angular difference between two compass bearings, 0–180.
export function bearingDiff(a, b) {
  const d = Math.abs(a - b) % 360;
  return d > 180 ? 360 - d : d;
}

// Project the vehicle onto the polyline (squared equirectangular metric —
// fine at city scale). Out-and-back routes traverse the same street twice
// with opposite bearings, so the nearest spot alone can land on the return
// leg and leave covered track blue. Every spot nearly as close as the best
// (within ~30 m) is a candidate; the vehicle's compass heading (when
// moving) and continuity with the previous split pick among them.
export function projectOnPath(path, v, prevProgress) {
  const kx = Math.cos(v.lat * Math.PI / 180);
  const spots = [];
  let bestD2 = Infinity;
  for (let i = 0; i < path.length - 1; i++) {
    const dx = (path[i + 1][0] - path[i][0]) * kx;
    const dy = path[i + 1][1] - path[i][1];
    const ax = (v.lon - path[i][0]) * kx;
    const ay = v.lat - path[i][1];
    const len2 = dx * dx + dy * dy;
    const t = len2 ? Math.min(1, Math.max(0, (ax * dx + ay * dy) / len2)) : 0;
    const ex = ax - t * dx;
    const ey = ay - t * dy;
    const d2 = ex * ex + ey * ey;
    if (d2 < bestD2) bestD2 = d2;
    spots.push({ i, t, d2,
                 bearing: (Math.atan2(dx, dy) * 180 / Math.PI + 360) % 360 });
  }
  let cands = spots.filter(
    s => s.d2 <= Math.max(bestD2 * 2.25, bestD2 + AMBIGUITY_M2));
  if (v.speed > 0) {
    const along = cands.filter(
      s => bearingDiff(s.bearing, v.direction || 0) <= 100);
    if (along.length) cands = along;
  }
  if (prevProgress == null) {
    cands.sort((a, b) => a.d2 - b.d2);
  } else {
    // the split may only move forward (2 segments of slack for GPS noise);
    // if the heading filter above vetoed every forward spot we mislocked
    // earlier — fall through and let it jump back to the right leg
    const forward = cands.filter(s => s.i + s.t >= prevProgress - 2);
    if (forward.length) cands = forward;
    cands.sort((a, b) => Math.abs(a.i + a.t - prevProgress)
                       - Math.abs(b.i + b.t - prevProgress));
  }
  const s = cands[0];
  const a = path[s.i];
  const b = path[s.i + 1];
  return { i: s.i, progress: s.i + s.t,
           meters: Math.sqrt(bestD2) * 111320, // true nearest, pre-filters
           point: [a[0] + s.t * (b[0] - a[0]), a[1] + s.t * (b[1] - a[1])] };
}

// Nearest stop on the vehicle's own route, out of the given stop list —
// used to anchor an off-route (e.g. depot) vehicle's trajectory at the point
// the trip will actually start, instead of a raw nearest shape point.
export function nearestRouteStop(stopsData, v) {
  const route = String(v.route || '?');
  const kx = Math.cos(v.lat * Math.PI / 180);
  let best = null;
  let bestD = Infinity;
  for (const s of stopsData) {
    if (!s.routes.includes(route)) continue;
    const dx = (s.lon - v.lon) * kx;
    const dy = s.lat - v.lat;
    const d = dx * dx + dy * dy;
    if (d < bestD) { bestD = d; best = s; }
  }
  return best;
}
