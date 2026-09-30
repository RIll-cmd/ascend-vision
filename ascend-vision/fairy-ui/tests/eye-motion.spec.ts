import { test, expect } from '@playwright/test';
import { stepEyeAnimation, type EyeAnimationState, type EyeMode } from '../src/lib/eye-animation';
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

test('eye motion remains finite and bounded through thirty seconds of activity', () => {
  let previous: EyeAnimationState | null = null;
  const speechPeaks: number[] = [];
  let lastScale = 1;
  let rising = false;

  for (let frame = 0; frame < 30 * 60; frame += 1) {
    const mode: EyeMode = frame < 600 ? 'idle' : frame < 1200 ? 'speaking' : 'listening';
    const state = stepEyeAnimation(previous, {
      mode, audioLevel: frame % 2, gaze: { x: 1, y: -1 }, reducedMotion: false,
    }, 1000 / 60);
    for (const value of [state.irisScale, state.glow, state.blink, state.gaze.x, state.gaze.y]) {
      expect(Number.isFinite(value)).toBe(true);
    }
    expect(state.irisScale).toBeGreaterThanOrEqual(0.94);
    expect(state.irisScale).toBeLessThanOrEqual(1.06);
    expect(state.glow).toBeGreaterThanOrEqual(0);
    expect(state.glow).toBeLessThanOrEqual(1);
    expect(state.blink).toBeGreaterThanOrEqual(0);
    expect(state.blink).toBeLessThanOrEqual(1);
    if (mode === 'speaking') {
      const nowRising = state.irisScale > lastScale;
      if (rising && !nowRising) speechPeaks.push(frame / 60);
      rising = nowRising;
    }
    lastScale = state.irisScale;
    previous = state;
  }

  expect(speechPeaks.length).toBeGreaterThanOrEqual(10);
  expect(speechPeaks.length).toBeLessThanOrEqual(22);
  expect(speechPeaks.every((peak, index) => index === 0 || peak - speechPeaks[index - 1] >= 1 / 2.2)).toBe(true);
});

test('speech ends smoothly and microphone input cannot drive speech motion', () => {
  let quiet: EyeAnimationState | null = null;
  let loud: EyeAnimationState | null = null;
  for (let frame = 0; frame < 30 * 60; frame += 1) {
    const mode: EyeMode = frame < 900 ? 'speaking' : 'idle';
    const nextQuiet = stepEyeAnimation(quiet, {
      mode, audioLevel: 0, gaze: { x: 0.8, y: -0.6 }, reducedMotion: false,
    }, 1000 / 60);
    const nextLoud = stepEyeAnimation(loud, {
      mode, audioLevel: frame % 2 ? 99 : -99,
      gaze: { x: 0.8, y: -0.6 }, reducedMotion: false,
    }, 1000 / 60);
    expect(Math.abs(nextQuiet.irisScale - (quiet?.irisScale ?? 1))).toBeLessThanOrEqual(0.01);
    expect(nextQuiet.irisScale).toBe(nextLoud.irisScale);
    expect(nextQuiet.glow).toBe(nextLoud.glow);
    quiet = nextQuiet;
    loud = nextLoud;
  }
});

test('reduced motion keeps the visible eye stable for thirty seconds', () => {
  let previous: EyeAnimationState | null = null;
  for (let frame = 0; frame < 30 * 60; frame += 1) {
    const state = stepEyeAnimation(previous, {
      mode: frame < 600 ? 'idle' : frame < 1200 ? 'speaking' : 'listening',
      audioLevel: frame % 2, gaze: { x: 1, y: -1 }, reducedMotion: true,
    }, 1000 / 60);
    expect([state.irisScale, state.glow, state.blink, state.gaze.x, state.gaze.y]).toEqual([1, 0.4, 0, 0, 0]);
    previous = state;
  }
});

test('frame time is clamped before advancing the animation clock', () => {
  const input = { mode: 'idle' as const, audioLevel: 0, gaze: { x: 0, y: 0 }, reducedMotion: false };
  const initial = stepEyeAnimation(null, input, -100);
  const next = stepEyeAnimation(initial, input, 1000);
  expect(initial.elapsedMs).toBe(0);
  expect(next.elapsedMs).toBe(50);
});

test('speaking holds the current gaze and never starts a blink', () => {
  let state: EyeAnimationState | null = null;
  for (let frame = 0; frame < 120; frame += 1) {
    state = stepEyeAnimation(state, {
      mode: 'listening', audioLevel: 0.7, gaze: { x: 0.6, y: -0.4 }, reducedMotion: false,
    }, 1000 / 60);
  }
  const heldGaze = state!.gaze;
  for (let frame = 0; frame < 30 * 60; frame += 1) {
    state = stepEyeAnimation(state, {
      mode: 'speaking', audioLevel: frame % 2, gaze: { x: -1, y: 1 }, reducedMotion: false,
    }, 1000 / 60);
    expect(state.gaze).toEqual(heldGaze);
    expect(state.blink).toBe(0);
  }
});

test('listening input levels and gaze stay bounded when telemetry is invalid', () => {
  const listening = (audioLevel: number, x: number) => stepEyeAnimation(null, {
    mode: 'listening', audioLevel, gaze: { x, y: Number.NaN }, reducedMotion: false,
  }, 50);
  const low = listening(-100, Number.POSITIVE_INFINITY);
  const high = listening(100, Number.POSITIVE_INFINITY);
  const atZero = listening(0, 1);
  const atOne = listening(1, 1);
  expect(low.glow).toBe(atZero.glow);
  expect(high.glow).toBe(atOne.glow);
  expect(atOne.glow).toBeGreaterThan(atZero.glow);
  expect(Number.isFinite(high.gaze.x)).toBe(true);
  expect(Number.isFinite(high.gaze.y)).toBe(true);
  expect(Math.abs(high.gaze.x)).toBeLessThanOrEqual(1);
});
