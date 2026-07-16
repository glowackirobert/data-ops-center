// Tests for static/js/geo.js — pure geometry, no DOM/deck.gl dependency, so
// this runs directly under Node's built-in test runner: node --test tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { projectOnPath, bearingDiff, nearestRouteStop, OFF_ROUTE_M }
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
