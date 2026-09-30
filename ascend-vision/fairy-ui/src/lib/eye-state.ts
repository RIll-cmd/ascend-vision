import type { EyeMode } from './eye-animation';
import type { EyeExpression } from './eye-motion';

export interface EyeModeSignals {
  runtime: boolean;
  connected: boolean;
  paused: boolean;
  speaking: boolean;
  thinking: boolean;
  listening: boolean;
}

/** Vision availability owns the eye mode; Core connectivity is deliberately absent. */
export function resolveEyeMode(signals: EyeModeSignals): EyeMode {
  if (signals.runtime && !signals.connected) return 'offline';
  if (signals.paused) return 'paused';
  if (signals.speaking) return 'speaking';
  if (signals.thinking) return 'thinking';
  if (signals.listening) return 'listening';
  return 'idle';
}

export function automaticEyeExpression(observation?: {
  emotion?: string | null;
  fatigue?: string | null;
  phoneBox?: unknown;
  posture?: string | null;
}): EyeExpression {
  if (observation?.emotion === 'smiling') return 'happy';
  if (observation?.emotion === 'fatigue' || observation?.fatigue?.includes('fatigue')) return 'sleepy';
  if (observation?.emotion === 'stressed') return 'sad';
  return 'open';
}
