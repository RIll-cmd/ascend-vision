"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

export interface BoundingBox {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  confidence: number;
  tag?: string;
  posture?: string | null;
}

export interface HandLandmarks {
  points: [number, number, number?][];
  handedness?: string;
}

export interface TargetingReticleProps extends React.SVGAttributes<SVGSVGElement> {
  phoneBox?: BoundingBox | null;
  hands?: HandLandmarks[];
  eyePoints?: [number, number][];
  lipPoints?: [number, number][];
  posture?: string | null;
  viewWidth?: number;
  viewHeight?: number;
  showLandmarks?: boolean;
}

const HOLD_INDICES = new Set([0, 4, 8, 12, 16, 20]);

export function TargetingReticle({
  phoneBox,
  hands = [],
  eyePoints = [],
  lipPoints = [],
  viewWidth = 640,
  viewHeight = 480,
  showLandmarks = true,
  className,
  ...props
}: TargetingReticleProps) {
  // Normalize coordinates if box is [0..1] vs pixel [0..viewWidth]
  const box = React.useMemo(() => {
    if (!phoneBox) return null;
    const isNormalized = phoneBox.x2 <= 1.05 && phoneBox.y2 <= 1.05;
    const scaleX = isNormalized ? viewWidth : 1;
    const scaleY = isNormalized ? viewHeight : 1;

    const x1 = phoneBox.x1 * scaleX;
    const y1 = phoneBox.y1 * scaleY;
    const x2 = phoneBox.x2 * scaleX;
    const y2 = phoneBox.y2 * scaleY;
    return {
      x1,
      y1,
      x2,
      y2,
      width: Math.max(10, x2 - x1),
      height: Math.max(10, y2 - y1),
      confidence: phoneBox.confidence,
      tag: phoneBox.tag || "CELL PHONE",
      posture: phoneBox.posture,
    };
  }, [phoneBox, viewWidth, viewHeight]);

  return (
    <svg
      data-testid="targeting-reticle"
      viewBox={`0 0 ${viewWidth} ${viewHeight}`}
      className={cn(
        "pointer-events-none absolute inset-0 h-full w-full select-none overflow-visible",
        className
      )}
      {...props}
    >
      <defs>
        <filter id="hud-glow-green" x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="0" stdDeviation="3" floodColor="#22c55e" floodOpacity="0.7" />
        </filter>
        <filter id="hud-glow-yellow" x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="0" stdDeviation="2" floodColor="#facc15" floodOpacity="0.8" />
        </filter>
        <filter id="hud-glow-cyan" x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="0" stdDeviation="2" floodColor="#38bdf8" floodOpacity="0.8" />
        </filter>
        <filter id="hud-glow-pink" x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="0" stdDeviation="2" floodColor="#f43f5e" floodOpacity="0.8" />
        </filter>
      </defs>

      {/* Outer Viewfinder Corner Brackets */}
      <g stroke="rgba(255,255,255,0.4)" strokeWidth="2" fill="none">
        {/* Top-Left */}
        <path d="M 12 28 L 12 12 L 28 12" />
        {/* Top-Right */}
        <path d={`M ${viewWidth - 28} 12 L ${viewWidth - 12} 12 L ${viewWidth - 12} 28`} />
        {/* Bottom-Left */}
        <path d={`M 12 ${viewHeight - 28} L 12 ${viewHeight - 12} L 28 ${viewHeight - 12}`} />
        {/* Bottom-Right */}
        <path
          d={`M ${viewWidth - 28} ${viewHeight - 12} L ${viewWidth - 12} ${viewHeight - 12} L ${
            viewWidth - 12
          } ${viewHeight - 28}`}
        />
      </g>

      {/* Subtle Grid crosshair in center */}
      <g stroke="rgba(255,255,255,0.15)" strokeWidth="1" strokeDasharray="3 3">
        <line x1={viewWidth / 2} y1={viewHeight / 2 - 20} x2={viewWidth / 2} y2={viewHeight / 2 + 20} />
        <line x1={viewWidth / 2 - 20} y1={viewHeight / 2} x2={viewWidth / 2 + 20} y2={viewHeight / 2} />
      </g>

      {/* Hand Landmarks: Gold fingertips, Cyan joints */}
      {showLandmarks &&
        hands.map((hand, hi) => (
          <g key={`hand-${hi}`}>
            {hand.points.map((pt, pi) => {
              const px = pt[0] <= 1.05 ? pt[0] * viewWidth : pt[0];
              const py = pt[1] <= 1.05 ? pt[1] * viewHeight : pt[1];
              const isHold = HOLD_INDICES.has(pi);

              return (
                <circle
                  key={`pt-${pi}`}
                  cx={px}
                  cy={py}
                  r={isHold ? 3.5 : 2.5}
                  fill={isHold ? "#00dcff" : "#38bdf8"}
                  stroke={isHold ? "#ffffff" : "#0284c7"}
                  strokeWidth="1"
                  filter={isHold ? "url(#hud-glow-cyan)" : undefined}
                />
              );
            })}
          </g>
        ))}

      {/* Face Eye Landmarks: Bright Yellow dots */}
      {showLandmarks &&
        eyePoints.map((pt, ei) => {
          const ex = pt[0] <= 1.05 ? pt[0] * viewWidth : pt[0];
          const ey = pt[1] <= 1.05 ? pt[1] * viewHeight : pt[1];
          return (
            <circle
              key={`eye-${ei}`}
              cx={ex}
              cy={ey}
              r="2"
              fill="#facc15"
              stroke="#ca8a04"
              strokeWidth="0.5"
              filter="url(#hud-glow-yellow)"
            />
          );
        })}

      {/* Face Mouth/Lip Landmarks: Magenta/Pink dots */}
      {showLandmarks &&
        lipPoints.map((pt, li) => {
          const lx = pt[0] <= 1.05 ? pt[0] * viewWidth : pt[0];
          const ly = pt[1] <= 1.05 ? pt[1] * viewHeight : pt[1];
          return (
            <circle
              key={`lip-${li}`}
              cx={lx}
              cy={ly}
              r="2"
              fill="#f43f5e"
              stroke="#be123c"
              strokeWidth="0.5"
              filter="url(#hud-glow-pink)"
            />
          );
        })}

      {/* Phone Bounding Box & HUD Reticle */}
      {box && (
        <g className="transition-all duration-150">
          {/* Main Glowing Frame */}
          <rect
            x={box.x1}
            y={box.y1}
            width={box.width}
            height={box.height}
            fill="rgba(34, 197, 94, 0.08)"
            stroke="#22c55e"
            strokeWidth="2"
            rx="4"
            filter="url(#hud-glow-green)"
          />

          {/* Corner Brackets on the Bounding Box */}
          <g stroke="#ffffff" strokeWidth="2.5" fill="none">
            <path d={`M ${box.x1} ${box.y1 + 10} L ${box.x1} ${box.y1} L ${box.x1 + 10} ${box.y1}`} />
            <path
              d={`M ${box.x2 - 10} ${box.y1} L ${box.x2} ${box.y1} L ${box.x2} ${box.y1 + 10}`}
            />
            <path
              d={`M ${box.x1} ${box.y2 - 10} L ${box.x1} ${box.y2} L ${box.x1 + 10} ${box.y2}`}
            />
            <path
              d={`M ${box.x2 - 10} ${box.y2} L ${box.x2} ${box.y2} L ${box.x2} ${box.y2 - 10}`}
            />
          </g>

          {/* Target Chip Header */}
          <g transform={`translate(${box.x1}, ${Math.max(22, box.y1 - 6)})`}>
            <rect
              x="0"
              y="-18"
              width={Math.min(180, Math.max(90, box.width))}
              height="20"
              fill="rgba(16, 24, 39, 0.85)"
              stroke="#22c55e"
              strokeWidth="1"
              rx="3"
            />
            <text
              x="6"
              y="-4"
              fill="#4ade80"
              fontSize="10"
              fontFamily="monospace"
              fontWeight="bold"
              letterSpacing="0.05em"
            >
              {`📱 ${(box.confidence * 100).toFixed(0)}%${
                box.posture && box.posture !== "NONE" ? ` [${box.posture.replace("PHONE_", "")}]` : ""
              }`}
            </text>
          </g>
        </g>
      )}
    </svg>
  );
}
