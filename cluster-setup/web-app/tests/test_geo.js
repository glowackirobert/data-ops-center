// Tests for static/js/geo.js — pure geometry, no DOM/deck.gl dependency, so
// this runs directly under Node's built-in test runner: node --test tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { projectOnPath, bearingDiff, nearestRouteStop, needsRecentre,
         OFF_ROUTE_M, FOLLOW_DEADZONE }
  from '../static/js/geo.js';

test('bearingDiff: opposite headings are 180 apart', () => {
  assert.equal(bearingDiff(0, 180), 180);
  assert.equal(bearingDiff(350, 10), 20);
  assert.equal(bearingDiff(10, 350), 20);
});

test('projectOnPath: projects onto the nearest segment when stationary', () => {
  const path = [[0, 0], [1, 0], [2, 0]];
  const v = { lat: 0.0001, lon: 1.5, speed: 0 };
  const proj = projectOnPath(path, v, null);
  assert.equal(proj.i, 1);
  assert.ok(Math.abs(proj.point[0] - 1.5) < 1e-6);
  assert.ok(proj.meters < OFF_ROUTE_M);
});

test('projectOnPath: out-and-back leg picked by heading when moving', () => {
  // Path goes east then back west over the same street (classic out-and-back).
  const path = [[0, 0], [1, 0], [0, 0]];
  // Heading ~270 (west) matches the return leg (segment index 1), not the
  // outbound leg (segment index 0, heading ~90/east).
  const v = { lat: 0.0001, lon: 0.5, speed: 20, direction: 270 };
  const proj = projectOnPath(path, v, null);
  assert.equal(proj.i, 1);
});

test('projectOnPath: forward-only continuity holds the line at the previous split', () => {
  const path = [[0, 0], [1, 0], [2, 0], [3, 0]];
  const v = { lat: 0.0001, lon: 1.5, speed: 0 };
  // previously at progress ~2.5 (near the end); a noisy reading near the
  // start should not snap the split backwards past the 2-segment slack.
  const proj = projectOnPath(path, v, 2.5);
  assert.ok(proj.progress >= 0.5, `expected forward-biased progress, got ${proj.progress}`);
});

test('nearestRouteStop: only considers stops serving the vehicle route', () => {
  const stops = [
    { lat: 0, lon: 0, routes: ['8'] },
    { lat: 0.0001, lon: 0.0001, routes: ['100'] }, // closer, wrong route
  ];
  const v = { lat: 0, lon: 0, route: '8' };
  const best = nearestRouteStop(stops, v);
  assert.equal(best.routes[0], '8');
});

test('nearestRouteStop: returns null when no stop serves the route', () => {
  const stops = [{ lat: 0, lon: 0, routes: ['100'] }];
  const v = { lat: 0, lon: 0, route: '8' };
  assert.equal(nearestRouteStop(stops, v), null);
});

// needsRecentre — the follow-camera deadzone. Boundaries are computed from
// FOLLOW_DEADZONE rather than hardcoded, so widening the deadzone doesn't
// silently invert what these assert.
const SIZE = { width: 1000, height: 500 };
const edgeX = SIZE.width * (1 - FOLLOW_DEADZONE) / 2;  // 200 at 0.6

test('needsRecentre: a centred vehicle never moves the camera', () => {
  assert.equal(needsRecentre({ x: 500, y: 250 }, SIZE), false);
});

test('needsRecentre: inside the deadzone stays put, outside pulls back', () => {
  assert.equal(needsRecentre({ x: edgeX + 10, y: 250 }, SIZE), false);
  assert.equal(needsRecentre({ x: edgeX - 10, y: 250 }, SIZE), true);
});

test('needsRecentre: the vertical edge is checked too, not just horizontal', () => {
  const edgeY = SIZE.height * (1 - FOLLOW_DEADZONE) / 2;
  assert.equal(needsRecentre({ x: 500, y: edgeY + 5 }, SIZE), false);
  assert.equal(needsRecentre({ x: 500, y: edgeY - 5 }, SIZE), true);
});

test('needsRecentre: a vehicle off-screen entirely is always pulled back', () => {
  assert.equal(needsRecentre({ x: -50, y: 250 }, SIZE), true);
  assert.equal(needsRecentre({ x: 500, y: SIZE.height + 200 }, SIZE), true);
});

test('needsRecentre: a wider deadzone tolerates more drift', () => {
  // Same point, two deadzones: 0.6 pulls it back, 0.98 leaves it alone.
  const p = { x: edgeX - 10, y: 250 };
  assert.equal(needsRecentre(p, SIZE, 0.6), true);
  assert.equal(needsRecentre(p, SIZE, 0.98), false);
});

test('needsRecentre: an unlaid-out container recentres rather than never firing', () => {
  // clientWidth/Height are 0 before the map is laid out; failing open means a
  // missed frame, failing closed would silently disable follow for the session.
  assert.equal(needsRecentre({ x: 0, y: 0 }, { width: 0, height: 0 }), true);
});
