"use client";

import React, { FC } from "react";
import { FairyEye, type EyeExpression } from "@/components/ui/fairy-eye";
import type { EyeMode } from "@/lib/eye-animation";
export type { EyeExpression };

export interface VoicePoweredOrbProps {
  className?: string;
  hue?: number;
  enableVoiceControl?: boolean;
  voiceSensitivity?: number;
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
  motionMode?: EyeMode;
}

/**
 * Compatibility wrapper for the Fairy Eye's bounded gaze, blink, and speech motion.
 */
export const VoicePoweredOrb: FC<VoicePoweredOrbProps> = ({
  className,
  hue = 0,
  enableVoiceControl = true,
  voiceSensitivity = 1.5,
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
      maxHoverIntensity={maxHoverIntensity}
      onVoiceDetected={onVoiceDetected}
      {...props}
    />
  );
};

export default VoicePoweredOrb;
