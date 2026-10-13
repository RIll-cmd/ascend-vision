export type EyeMode = 'idle' | 'listening' | 'thinking' | 'speaking' | 'paused' | 'offline';

export interface EyeAnimationInput {
  mode: EyeMode;
  audioLevel: number;
  gaze: { x: number; y: number };
  faceTracking?: boolean;
  reducedMotion: boolean;
}

export interface EyeAnimationState {
  irisScale: number;
  glow: number;
  blink: number;
  gaze: { x: number; y: number };
  ringRotation: number;
  /** Spiked ring speed in radians per millisecond; eases between calm and speaking. */
  ringSpeed: number;
  /** 0..1 speaking intensity: drives the faster ring, sharper spikes and the voice pulse. */
  voice: number;
  /** Where the eye is currently fixed; the gaze darts here in a quick saccade. */
  fixation: { x: number; y: number };
  nextGlanceMs: number;
  glances: number;
  elapsedMs: number;
}

export const CALM_RING_SPEED = Math.PI * 2 / 12_000;
export const SPEAKING_RING_SPEED = Math.PI * 2 / 6_500;
/** Largest gaze when tracking a face; the renderer foreshortens the iris at the edge. */
export const FACE_GAZE_LIMIT = 0.72;
const SACCADE_JUMP = 0.045;

function clampUnit(value: number): number {
  return Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));
}

function clampGaze(value: number): number {
  return Math.min(1, Math.max(-1, Number.isFinite(value) ? value : 0));
}

/** Deterministic 0..1 noise so the motion is repeatable in tests. */
function hash(seed: number): number {
  const value = Math.sin(seed * 12.9898 + 78.233) * 43_758.5453;
  return value - Math.floor(value);
}

function limit(point: { x: number; y: number }, radius: number) {
  const length = Math.hypot(point.x, point.y);
  return length > radius ? { x: point.x * radius / length, y: point.y * radius / length } : point;
}

/** A syllable-like speaking envelope (no audio needed), 0..1. */
export function speechEnvelope(ms: number): number {
  const t = ms / 1000;
  const syllables = 0.5 + 0.5 * Math.sin(t * 2 * Math.PI * 4.3) * Math.sin(t * 2 * Math.PI * 1.7 + 0.6);
  return clampUnit(0.35 + 0.65 * syllables);
}

function desiredGaze(input: EyeAnimationInput, elapsedMs: number) {
  const raw = { x: clampGaze(input.gaze.x), y: clampGaze(input.gaze.y) };
  if (input.faceTracking) {
    // Like Fairy on her monitor: the whole eyeball turns toward you.
    return limit({ x: raw.x * 0.9, y: raw.y * 0.9 }, FACE_GAZE_LIMIT);
  }
  if (input.mode === 'listening' || input.mode === 'speaking') return { x: raw.x * 0.2, y: raw.y * 0.2 };
  if (input.mode === 'thinking') {
    // Looks up and aside while thinking.
    const side = Math.sin(elapsedMs / 900) > 0 ? 0.16 : -0.12;
    return { x: side, y: -0.14 };
  }
  const idleGaze = { x: raw.x * 0.12, y: raw.y * 0.12 };
  const interval = 6_200;
  const phase = elapsedMs % interval;
  if (phase > 450 && phase < 5_250) {
    const seed = Math.floor(elapsedMs / interval);
    const sample = (offset: number) => (hash(seed + offset) - 0.5) * 0.24;
    return {
      x: Math.max(-0.12, Math.min(0.12, sample(3.1) + idleGaze.x)),
      y: Math.max(-0.12, Math.min(0.12, sample(9.7) + idleGaze.y)),
    };
  }
  return idleGaze;
}

export function stepEyeAnimation(
  previous: EyeAnimationState | null,
  input: EyeAnimationInput,
  dtMs: number,
): EyeAnimationState {
  const dt = Math.min(50, Math.max(0, Number.isFinite(dtMs) ? dtMs : 0));
  const elapsedMs = (previous?.elapsedMs ?? 0) + dt;
  const base = {
    ringRotation: previous?.ringRotation ?? 0,
    nextGlanceMs: previous?.nextGlanceMs ?? 0,
    glances: previous?.glances ?? 0,
    elapsedMs,
  };
  if (input.reducedMotion || input.mode === 'paused' || input.mode === 'offline') {
    const center = { x: 0, y: 0 };
    return { ...base, irisScale: 1, glow: 0.4, blink: 0, gaze: center, fixation: center, ringSpeed: 0, voice: 0 };
  }

  const speaking = input.mode === 'speaking';
  const voiceTarget = speaking ? 1 : 0;
  const previousVoice = previous?.voice ?? 0;
  const voice = previousVoice + (voiceTarget - previousVoice) * (1 - Math.exp(-dt / (speaking ? 220 : 600)));
  const pulse = voice * speechEnvelope(elapsedMs);

  const seconds = elapsedMs / 1000;
  const breathing = input.mode === 'idle' ? 0.018 * Math.sin(seconds * 2 * Math.PI * 0.18) : 0;
  const targetScale = 1 + breathing + pulse * 0.022;
  const targetGlow = 0.4 + (input.mode === 'listening' ? 0.05 * clampUnit(input.audioLevel) : 0) + pulse * 0.14;
  const scale = previous?.irisScale ?? 1;
  const glow = previous?.glow ?? 0.4;
  const scaleEase = 1 - Math.exp(-dt / (targetScale > scale ? 90 : 220));
  const glowEase = 1 - Math.exp(-dt / (targetGlow > glow ? 90 : 220));

  // Saccades: hold a fixation, then dart to the new point when the target moves or a glance is due.
  const desired = desiredGaze(input, elapsedMs);
  let fixation = previous?.fixation ?? { x: 0, y: 0 };
  let { nextGlanceMs, glances } = base;
  const tracking = Boolean(input.faceTracking) || input.mode === 'listening' || speaking;
  if (Math.hypot(desired.x - fixation.x, desired.y - fixation.y) > SACCADE_JUMP) {
    fixation = desired;
    nextGlanceMs = elapsedMs + 700 + hash(glances + 0.5) * 900;
    glances += 1;
  } else if (tracking && elapsedMs >= nextGlanceMs) {
    // Small looks around your face so she never feels frozen while watching you.
    const reach = input.faceTracking ? 0.05 : 0.025;
    fixation = limit({ x: desired.x + (hash(glances + 1.3) - 0.5) * 2 * reach,
      y: desired.y + (hash(glances + 7.1) - 0.5) * 2 * reach }, FACE_GAZE_LIMIT);
    nextGlanceMs = elapsedMs + 700 + hash(glances + 2.9) * 1100;
    glances += 1;
  } else if (!tracking) {
    fixation = desired;
  }
  const gazeEase = 1 - Math.exp(-dt / (tracking ? 45 : 240));
  const previousGaze = previous?.gaze ?? { x: 0, y: 0 };

  const blinkPhase = elapsedMs % 4600;
  const blink = blinkPhase >= 180 ? 0 : 1 - Math.abs(blinkPhase - 90) / 90;
  const targetSpeed = CALM_RING_SPEED + (SPEAKING_RING_SPEED - CALM_RING_SPEED) * voice;
  const ringSpeed = (previous?.ringSpeed ?? CALM_RING_SPEED) + (targetSpeed - (previous?.ringSpeed ?? CALM_RING_SPEED)) * (1 - Math.exp(-dt / 350));
  return {
    irisScale: scale + (targetScale - scale) * scaleEase,
    glow: glow + (targetGlow - glow) * glowEase,
    blink,
    gaze: {
      x: previousGaze.x + (fixation.x - previousGaze.x) * gazeEase,
      y: previousGaze.y + (fixation.y - previousGaze.y) * gazeEase,
    },
    fixation,
    nextGlanceMs,
    glances,
    ringSpeed,
    voice,
    ringRotation: ((base.ringRotation) + dt * ringSpeed) % (Math.PI * 2),
    elapsedMs,
  };
}
