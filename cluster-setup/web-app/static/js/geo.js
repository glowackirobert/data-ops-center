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
