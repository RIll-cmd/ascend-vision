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
  elapsedMs: number;
}

function clampUnit(value: number): number {
  return Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));
}

function clampGaze(value: number): number {
  return Math.min(1, Math.max(-1, Number.isFinite(value) ? value : 0));
}

export function stepEyeAnimation(
  previous: EyeAnimationState | null,
  input: EyeAnimationInput,
  dtMs: number,
): EyeAnimationState {
  const dt = Math.min(50, Math.max(0, Number.isFinite(dtMs) ? dtMs : 0));
  const elapsedMs = (previous?.elapsedMs ?? 0) + dt;
  if (input.reducedMotion || input.mode === 'paused' || input.mode === 'offline') {
    return { irisScale: 1, glow: 0.4, blink: 0, gaze: { x: 0, y: 0 }, ringRotation: previous?.ringRotation ?? 0, elapsedMs };
  }
  if (input.mode === 'speaking') {
    return {
      irisScale: previous?.irisScale ?? 1,
      glow: previous?.glow ?? 0.4,
      blink: 0,
      gaze: previous?.gaze ?? { x: 0, y: 0 },
      ringRotation: previous?.ringRotation ?? 0,
      elapsedMs,
    };
  }
  const seconds = elapsedMs / 1000;
  const targetScale = input.mode === 'idle' ? 1 + 0.018 * Math.sin(seconds * 2 * Math.PI * 0.18) : 1;
  const targetGlow = input.mode === 'listening' ? 0.4 + 0.05 * clampUnit(input.audioLevel) : 0.4;
  const scale = previous?.irisScale ?? 1;
  const glow = previous?.glow ?? 0.4;
  const scaleEase = 1 - Math.exp(-dt / (targetScale > scale ? 130 : 260));
  const glowEase = 1 - Math.exp(-dt / (targetGlow > glow ? 130 : 260));
  const gazeEase = 1 - Math.exp(-dt / 240);
  let targetGaze = { x: 0, y: 0 };
  if (input.faceTracking) {
    // Face attention is more visible than idle glances, but remains inside the optical socket.
    const faceGaze = { x: clampGaze(input.gaze.x) * 0.55, y: clampGaze(input.gaze.y) * 0.55 };
    const magnitude = Math.hypot(faceGaze.x, faceGaze.y);
    const scale = magnitude > 0.42 ? 0.42 / magnitude : 1;
    targetGaze = { x: faceGaze.x * scale, y: faceGaze.y * scale };
  } else if (input.mode === 'listening') {
    // Eye attention moves within a small inner-group range; the orb shell stays fixed.
    targetGaze = { x: clampGaze(input.gaze.x) * 0.2, y: clampGaze(input.gaze.y) * 0.2 };
  } else if (input.mode === 'idle') {
    const idleGaze = { x: clampGaze(input.gaze.x) * 0.12, y: clampGaze(input.gaze.y) * 0.12 };
    const interval = 6_200;
    const phase = elapsedMs % interval;
    if (phase > 450 && phase < 5_250) {
      const seed = Math.floor(elapsedMs / interval);
      const sample = (offset: number) => {
        const value = Math.sin(seed * 12.9898 + offset) * 43_758.5453;
        return (value - Math.floor(value) - 0.5) * 0.24;
      };
      targetGaze = {
        x: Math.max(-0.12, Math.min(0.12, sample(3.1) + idleGaze.x)),
        y: Math.max(-0.12, Math.min(0.12, sample(9.7) + idleGaze.y)),
      };
    } else {
      targetGaze = idleGaze;
    }
  }
  const blinkPhase = elapsedMs % 4600;
  const blink = blinkPhase >= 180 ? 0 : 1 - Math.abs(blinkPhase - 90) / 90;
  return {
    irisScale: scale + (targetScale - scale) * scaleEase,
    glow: glow + (targetGlow - glow) * glowEase,
    blink,
    gaze: {
      x: (previous?.gaze.x ?? 0) + (targetGaze.x - (previous?.gaze.x ?? 0)) * gazeEase,
      y: (previous?.gaze.y ?? 0) + (targetGaze.y - (previous?.gaze.y ?? 0)) * gazeEase,
    },
    ringRotation: ((previous?.ringRotation ?? 0) + dt * Math.PI * 2 / 12_000) % (Math.PI * 2),
    elapsedMs,
  };
}
