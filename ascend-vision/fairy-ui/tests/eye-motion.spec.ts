import { test, expect } from '@playwright/test';
import { constrainGaze, SOCKET_RADIUS, stepSpring } from '../src/lib/eye-motion';

test('circular socket bounds diagonal gaze and directional recoil', () => {
  for (const target of [{ x: 2, y: 2 }, { x: -4, y: 0 }, { x: 0, y: -9 }]) {
    const { pupil, drag } = constrainGaze(target);
    expect(Math.hypot(pupil.x, pupil.y)).toBeCloseTo(SOCKET_RADIUS);
    expect(Math.hypot(drag.x, drag.y)).toBeCloseTo(18);
    expect(pupil.x * drag.y - pupil.y * drag.x).toBeCloseTo(0);
  }
  expect(constrainGaze({ x: .1, y: .2 }).drag).toEqual({ x: 0, y: 0 });
});

test('spring stays bounded and returns to rest after border pressure', () => {
  const position = { x: 0, y: 0 }, velocity = { x: 0, y: 0 };
  for (let i = 0; i < 120; i++) stepSpring(position, velocity, { x: 18, y: 0 }, 1 / 60);
  expect(position.x).toBeCloseTo(18, 1);
  for (let i = 0; i < 180; i++) {
    stepSpring(position, velocity, { x: 0, y: 0 }, 1 / 60);
    expect(Math.hypot(position.x, position.y)).toBeLessThanOrEqual(20);
  }
  expect(Math.abs(position.x)).toBeLessThan(.01);
});
