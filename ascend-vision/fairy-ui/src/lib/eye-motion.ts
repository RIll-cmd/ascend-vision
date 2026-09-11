export type EyeExpression =
  | 'open'
  | 'happy'
  | 'sleepy'
  | 'sad'
  | 'focus'
  | 'stern'
  | 'curious'
  | 'surprised'
  | 'half'
  | 'squint'
  | 'angry'
  | 'disappointed';

export interface Point { x: number; y: number }

export interface ExpressionPalette {
  base: string;
  glow: string;
  core: string;
}

export interface ExpressionConfig {
  lid: number;
  lowerLid: number;
  lidSlant: number;
  focus: number;
  tempo?: number;
  dilation?: number;
  palette?: ExpressionPalette;
}

export const EXPRESSIONS: Record<EyeExpression, ExpressionConfig> = {
  // normal = original color (inherits props.baseColor / active theme)
  open: {
    lid: 0.65,
    lowerLid: 0.0,
    lidSlant: 0.0,
    focus: 0.0,
    tempo: 1.0,
    dilation: 0.0,
  },
  // happy = yellow
  happy: {
    lid: 0.55,
    lowerLid: 0.38,
    lidSlant: -0.08,
    focus: 0.1,
    tempo: 1.35,
    dilation: 0.12,
    palette: { base: '#634304', glow: '#ffc814', core: '#fffde8' },
  },
  // sleepy = cozy twilight lavender
  sleepy: {
    lid: 0.06,
    lowerLid: 0.24,
    lidSlant: 0.0,
    focus: -0.1,
    tempo: 0.55,
    dilation: -0.05,
    palette: { base: '#1f1b3c', glow: '#7d6bcf', core: '#f2efff' },
  },
  // sad = other shade of blue (deep melancholic sapphire/cobalt)
  sad: {
    lid: 0.28,
    lowerLid: 0.05,
    lidSlant: -0.42,
    focus: -0.05,
    tempo: 0.75,
    dilation: 0.06,
    palette: { base: '#0b1d40', glow: '#3867d6', core: '#cbdfff' },
  },
  // focus = precision laser emerald
  focus: {
    lid: 0.65,
    lowerLid: 0.0,
    lidSlant: 0.0,
    focus: 1.0,
    tempo: 1.1,
    dilation: -0.15,
    palette: { base: '#063d33', glow: '#00e5a3', core: '#e6fff7' },
  },
  // stern = red (furrowed brow)
  stern: {
    lid: 0.12,
    lowerLid: 0.0,
    lidSlant: 0.40,
    focus: 0.4,
    tempo: 1.25,
    dilation: -0.1,
    palette: { base: '#5c0d11', glow: '#ff2e3b', core: '#ffdede' },
  },
  // angry = red (fierce furrowed glare)
  angry: {
    lid: 0.10,
    lowerLid: 0.0,
    lidSlant: 0.44,
    focus: 0.45,
    tempo: 1.3,
    dilation: -0.1,
    palette: { base: '#5c0d11', glow: '#ff2e3b', core: '#ffdede' },
  },
  // curious = mystery violet
  curious: {
    lid: 0.60,
    lowerLid: 0.08,
    lidSlant: 0.22,
    focus: -0.15,
    tempo: 1.2,
    dilation: 0.22,
    palette: { base: '#3b1854', glow: '#b854ff', core: '#faedff' },
  },
  // surprised = light blue (electric cyan-ice)
  surprised: {
    lid: 0.88,
    lowerLid: 0.0,
    lidSlant: 0.0,
    focus: -0.2,
    tempo: 1.45,
    dilation: 0.38,
    palette: { base: '#0d4f6c', glow: '#5ce1e6', core: '#f0fdff' },
  },
  // half = neutral muted slate
  half: {
    lid: 0.18,
    lowerLid: 0.0,
    lidSlant: 0.0,
    focus: 0.0,
    tempo: 1.0,
    dilation: 0.0,
    palette: { base: '#242d38', glow: '#78889b', core: '#e8eef5' },
  },
  // squint = grey (disappointed squint)
  squint: {
    lid: -0.14,
    lowerLid: 0.15,
    lidSlant: 0.0,
    focus: 0.0,
    tempo: 1.0,
    dilation: 0.0,
    palette: { base: '#2b313a', glow: '#8f9baa', core: '#edf2f7' },
  },
  // disappointed = grey
  disappointed: {
    lid: -0.16,
    lowerLid: 0.18,
    lidSlant: 0.0,
    focus: 0.0,
    tempo: 0.85,
    dilation: 0.0,
    palette: { base: '#2b313a', glow: '#8f9baa', core: '#edf2f7' },
  },
};
export const SOCKET_RADIUS = .72;
export const MAX_RECOIL = 18;
export function constrainGaze(target: Point) {
  const x = Number.isFinite(target.x) ? target.x : 0;
  const y = Number.isFinite(target.y) ? target.y : 0;
  const length = Math.hypot(x, y);
  const unit = length > 0 ? { x: x / length, y: y / length } : { x: 0, y: 0 };
  const distance = Math.min(length, SOCKET_RADIUS);
  const push = Math.min(1, Math.max(0, (length - SOCKET_RADIUS) / .28));
  return { pupil: { x: unit.x * distance, y: unit.y * distance }, drag: { x: unit.x * push * MAX_RECOIL, y: unit.y * push * MAX_RECOIL } };
}
export function stepSpring(position: Point, velocity: Point, target: Point, dt: number) {
  // Small substeps keep elastic drag stable after dropped frames and tab restoration.
  let remaining = Math.min(Math.max(dt, 0), .05);
  while (remaining > 0) {
    const step = Math.min(remaining, 1 / 120);
    for (const axis of ['x', 'y'] as const) {
      velocity[axis] += ((target[axis] - position[axis]) * 145 - velocity[axis] * 19) * step;
      position[axis] += velocity[axis] * step;
    }
    remaining -= step;
  }
  const length = Math.hypot(position.x, position.y);
  if (length > 20) { position.x *= 20 / length; position.y *= 20 / length; }
}
