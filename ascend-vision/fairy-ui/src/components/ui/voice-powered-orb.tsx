"use client";

import React, { FC } from "react";
import { FairyEye, type FairyEyeProps, type EyeExpression } from "@/components/ui/fairy-eye";
export type { EyeExpression };

export interface VoicePoweredOrbProps {
  className?: string;
  hue?: number;
  enableVoiceControl?: boolean;
  voiceSensitivity?: number;
  maxRotationSpeed?: number;
  maxHoverIntensity?: number;
  onVoiceDetected?: (detected: boolean) => void;
  // Extended Fairy features
  pupilOffset?: { x: number; y: number };
  irisScale?: number;
  dilation?: number;
  baseColor?: string;
  glowColor?: string;
  coreColor?: string;
  audioLevel?: number;
  onAudioError?: (message: string) => void;
  reducedMotion?: boolean;
  expression?: EyeExpression;
  audioReactive?: boolean;
  speaking?: boolean;
}

/**
 * VoicePoweredOrb upgraded with the canonical Fairy AI idle loop and speech amplification.
 * Features:
 * - Idle harmonic pendulum sway on the satellite dot
 * - 3-5% smooth concentric ring breathing (3-4s cycle)
 * - 4-corner aperture notch continuous micro-spin and organic wave displacement
 * - Real-time speech dilation boost (up to 25% aperture expansion)
 * - High-frequency acoustic waveform ripple across ring perimeters
 * - Satellite flare, bloom, and orbital acceleration during vocal spikes
 * - Asymmetric attack/decay audio smoothing and face tracking preservation
 */
export const VoicePoweredOrb: FC<VoicePoweredOrbProps> = ({
  className,
  hue = 0,
  enableVoiceControl = true,
  voiceSensitivity = 1.5,
  maxRotationSpeed = 1.2,
  maxHoverIntensity = 0.8,
  onVoiceDetected,
  ...props
}) => {
  return (
    <FairyEye
      className={className}
      hue={hue}
      enableVoiceControl={enableVoiceControl}
      voiceSensitivity={voiceSensitivity}
      maxRotationSpeed={maxRotationSpeed}
      maxHoverIntensity={maxHoverIntensity}
      onVoiceDetected={onVoiceDetected}
      {...props}
    />
  );
};

export default VoicePoweredOrb;
