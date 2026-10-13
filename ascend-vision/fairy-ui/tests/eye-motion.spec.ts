import { test, expect } from '@playwright/test';
import { CALM_RING_SPEED, FACE_GAZE_LIMIT, SPEAKING_RING_SPEED, stepEyeAnimation, type EyeAnimationState, type EyeMode } from '../src/lib/eye-animation';
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

  for (let frame = 0; frame < 30 * 60; frame += 1) {
    const mode: EyeMode = frame < 600 ? 'idle' : frame < 1200 ? 'speaking' : 'listening';
    const state = stepEyeAnimation(previous, {
      mode, audioLevel: frame % 2, gaze: { x: 1, y: -1 }, reducedMotion: false,
    }, 1000 / 60);
    for (const value of [state.irisScale, state.glow, state.blink, state.gaze.x, state.gaze.y, state.ringRotation]) {
      expect(Number.isFinite(value)).toBe(true);
    }
    expect(state.ringRotation).toBeGreaterThanOrEqual(0);
    expect(state.ringRotation).toBeLessThan(2 * Math.PI);
    expect(state.irisScale).toBeGreaterThanOrEqual(0.94);
    expect(state.irisScale).toBeLessThanOrEqual(1.06);
    expect(state.glow).toBeGreaterThanOrEqual(0);
    expect(state.glow).toBeLessThanOrEqual(1);
    expect(state.blink).toBeGreaterThanOrEqual(0);
    expect(state.blink).toBeLessThanOrEqual(1);
    previous = state;
  }
});

test('speaking keeps the eye alive but microphone input cannot drive it', () => {
  let quiet: EyeAnimationState | null = null;
  let loud: EyeAnimationState | null = null;
  const gazes = new Set<string>();
  for (let frame = 0; frame < 30 * 60; frame += 1) {
    const mode: EyeMode = frame < 900 ? 'speaking' : 'idle';
    const nextQuiet = stepEyeAnimation(quiet, {
      mode, audioLevel: 0, gaze: { x: 0.8, y: -0.6 }, reducedMotion: false,
    }, 1000 / 60);
    const nextLoud = stepEyeAnimation(loud, {
      mode, audioLevel: frame % 2 ? 99 : -99,
      gaze: { x: 0.8, y: -0.6 }, reducedMotion: false,
    }, 1000 / 60);
    expect(nextQuiet.irisScale).toBe(nextLoud.irisScale);
    expect(nextQuiet.glow).toBe(nextLoud.glow);
    if (mode === 'speaking') gazes.add(`${nextQuiet.gaze.x.toFixed(3)},${nextQuiet.gaze.y.toFixed(3)}`);
    else expect(Math.abs(nextQuiet.irisScale - quiet!.irisScale)).toBeLessThanOrEqual(0.01);
    quiet = nextQuiet;
    loud = nextLoud;
  }
  // Small looks around while talking: never frozen on one point.
  expect(gazes.size).toBeGreaterThan(10);
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

test('speaking keeps following the target, still blinks and pulses gently', () => {
  let state: EyeAnimationState | null = null;
  let blinked = false;
  let minGlow = 1, maxGlow = 0;
  for (let frame = 0; frame < 10 * 60; frame += 1) {
    state = stepEyeAnimation(state, {
      mode: 'speaking', audioLevel: frame % 2, gaze: { x: -1, y: 1 }, reducedMotion: false,
    }, 1000 / 60);
    if (state.blink > 0.5) blinked = true;
    if (frame > 120) { minGlow = Math.min(minGlow, state.glow); maxGlow = Math.max(maxGlow, state.glow); }
  }
  expect(state!.gaze.x).toBeLessThan(-0.1);
  expect(state!.gaze.y).toBeGreaterThan(0.1);
  expect(blinked).toBe(true);
  expect(maxGlow - minGlow).toBeGreaterThan(0.02);
  expect(maxGlow).toBeLessThanOrEqual(0.56);
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

test('idle eye attention stays inside two percent and listening gaze is bounded at common frame rates', () => {
  for (const fps of [30, 60, 120]) {
    let state: EyeAnimationState | null = null;
    const step = 1000 / fps;
    for (let frame = 0; frame < 9 * fps; frame += 1) {
      state = stepEyeAnimation(state, {
        mode: 'idle', audioLevel: 0, gaze: { x: 1, y: -1 }, reducedMotion: false,
      }, step);
      expect(Math.hypot(state.gaze.x, state.gaze.y)).toBeLessThanOrEqual(.18);
    }
    state = stepEyeAnimation(state, {
      mode: 'listening', audioLevel: 0, gaze: { x: 1, y: -1 }, reducedMotion: false,
    }, step);
    expect(Math.hypot(state.gaze.x, state.gaze.y)).toBeLessThanOrEqual(.2 * Math.SQRT2);
  }
});

test('idle gaze uses deterministic timing and returns to center between glances', () => {
  const run = () => {
    let state: EyeAnimationState | null = null;
    const snapshots: { x: number; y: number }[] = [];
    for (let frame = 0; frame < 15 * 60; frame += 1) {
      state = stepEyeAnimation(state, {
        mode: 'idle', audioLevel: 0, gaze: { x: 0, y: 0 }, reducedMotion: false,
      }, 1000 / 60);
      if (frame % 60 === 0) snapshots.push(state.gaze);
    }
    return snapshots;
  };
  expect(run()).toEqual(run());
  expect(run().some(gaze => Math.hypot(gaze.x, gaze.y) > .01)).toBe(true);
});

test('face tracking turns the whole eye toward the face while staying inside its socket', () => {
  const face = stepEyeAnimation(null, {
    mode: 'idle', audioLevel: 0, gaze: { x: 0.8, y: -0.4 }, faceTracking: true, reducedMotion: false,
  }, 50);
  const pointer = stepEyeAnimation(null, {
    mode: 'idle', audioLevel: 0, gaze: { x: 0.8, y: -0.4 }, reducedMotion: false,
  }, 50);

  expect(face.gaze.x).toBeGreaterThan(pointer.gaze.x * 2);
  expect(face.gaze.y).toBeLessThan(pointer.gaze.y * 2);
  let state: EyeAnimationState | null = null;
  for (let frame = 0; frame < 20 * 60; frame += 1) {
    state = stepEyeAnimation(state, {
      mode: frame % 600 < 300 ? 'idle' : 'speaking', audioLevel: 0, gaze: { x: 3, y: -3 }, faceTracking: true, reducedMotion: false,
    }, 1000 / 60);
    expect(Math.hypot(state.gaze.x, state.gaze.y)).toBeLessThanOrEqual(FACE_GAZE_LIMIT + 1e-9);
  }
});

test('face tracking darts quickly to a new position like a saccade', () => {
  let state: EyeAnimationState | null = null;
  for (let frame = 0; frame < 60; frame += 1) {
    state = stepEyeAnimation(state, { mode: 'idle', audioLevel: 0, gaze: { x: -0.6, y: 0 }, faceTracking: true, reducedMotion: false }, 1000 / 60);
  }
  for (let frame = 0; frame < 9; frame += 1) {
    state = stepEyeAnimation(state, { mode: 'idle', audioLevel: 0, gaze: { x: 0.6, y: 0 }, faceTracking: true, reducedMotion: false }, 1000 / 60);
  }
  // Within 150 ms the eye has crossed most of the way.
  expect(state!.gaze.x).toBeGreaterThan(0.35);
});

test('spiked ring turns calmly, speeds up while speaking, and holds its angle when reduced', () => {
  const input = { mode: 'idle' as const, audioLevel: 0, gaze: { x: 0, y: 0 }, reducedMotion: false };
  const initial = stepEyeAnimation(null, input, 50);
  const moving = stepEyeAnimation(initial, input, 50);
  const reduced = stepEyeAnimation(moving, { ...input, reducedMotion: true }, 50);

  expect(moving.ringRotation).toBeGreaterThan(initial.ringRotation);
  expect(moving.ringRotation - initial.ringRotation).toBeCloseTo(50 * CALM_RING_SPEED, 8);
  expect(reduced.ringRotation).toBe(moving.ringRotation);

  let speaking = moving;
  for (let frame = 0; frame < 120; frame += 1) speaking = stepEyeAnimation(speaking, { ...input, mode: 'speaking' }, 1000 / 60);
  expect(speaking.ringSpeed).toBeGreaterThan(CALM_RING_SPEED * 1.6);
  expect(speaking.ringSpeed).toBeLessThanOrEqual(SPEAKING_RING_SPEED + 1e-12);
  expect(speaking.voice).toBeGreaterThan(0.9);
  let calm = speaking;
  for (let frame = 0; frame < 240; frame += 1) calm = stepEyeAnimation(calm, input, 1000 / 60);
  expect(calm.ringSpeed).toBeLessThan(CALM_RING_SPEED * 1.1);
});
