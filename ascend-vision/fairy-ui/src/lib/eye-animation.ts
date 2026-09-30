export type EyeMode = 'idle' | 'listening' | 'thinking' | 'speaking' | 'paused' | 'offline';

export interface EyeAnimationInput {
  mode: EyeMode;
  audioLevel: number;
  gaze: { x: number; y: number };
  reducedMotion: boolean;
}

export interface EyeAnimationState {
  irisScale: number;
  glow: number;
  blink: number;
  gaze: { x: number; y: number };
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
    return { irisScale: 1, glow: 0.4, blink: 0, gaze: { x: 0, y: 0 }, elapsedMs };
  }
  const seconds = elapsedMs / 1000;
  const speechPulse = (1 + Math.sin(seconds * 2 * Math.PI * 1.4)) / 2;
  const targetScale = input.mode === 'speaking'
    ? 1 + 0.04 * speechPulse
    : input.mode === 'idle' ? 1 + 0.018 * Math.sin(seconds * 2 * Math.PI * 0.18) : 1;
  const targetGlow = input.mode === 'speaking' ? 0.4 + 0.08 * speechPulse
    : input.mode === 'listening' ? 0.4 + 0.05 * clampUnit(input.audioLevel) : 0.4;
  const scale = previous?.irisScale ?? 1;
  const glow = previous?.glow ?? 0.4;
  const scaleEase = 1 - Math.exp(-dt / (targetScale > scale ? 130 : 260));
  const glowEase = 1 - Math.exp(-dt / (targetGlow > glow ? 130 : 260));
  const gazeEase = 1 - Math.exp(-dt / 240);
  const targetGaze = input.mode === 'speaking'
    ? (previous?.gaze ?? { x: 0, y: 0 })
    : input.mode === 'listening'
      ? { x: clampGaze(input.gaze.x), y: clampGaze(input.gaze.y) }
      : { x: 0, y: 0 };
  const blinkPhase = elapsedMs % 4600;
  const blink = input.mode === 'speaking' || blinkPhase >= 180
    ? 0 : 1 - Math.abs(blinkPhase - 90) / 90;
  return {
    irisScale: scale + (targetScale - scale) * scaleEase,
    glow: glow + (targetGlow - glow) * glowEase,
    blink,
    gaze: {
      x: (previous?.gaze.x ?? 0) + (targetGaze.x - (previous?.gaze.x ?? 0)) * gazeEase,
      y: (previous?.gaze.y ?? 0) + (targetGaze.y - (previous?.gaze.y ?? 0)) * gazeEase,
    },
    elapsedMs,
  };
}
