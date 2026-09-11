import * as React from "react";
import { cn } from "@/lib/utils";

export interface BorderBeamProps {
  className?: string;
  colorFrom?: string;
  colorTo?: string;
  borderWidth?: number;
  duration?: number;
  size?: number;
  active?: boolean;
}

export function BorderBeam({
  className,
  colorFrom = "#f59e0b", // Amber by default for warning alerts
  colorTo = "#f43f5e",   // Rose/Coral
  borderWidth = 2,
  duration = 4,
  size = 100,
  active = true,
}: BorderBeamProps) {
  if (!active) return null;

  const id = React.useId().replace(/:/g, "");

  return (
    <div className={cn("pointer-events-none absolute inset-0 rounded-[inherit] overflow-hidden", className)}>
      <svg
        className="absolute inset-0 h-full w-full"
        xmlns="http://www.w3.org/2000/svg"
      >
        <defs>
          <linearGradient id={`beam-grad-${id}`} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor={colorFrom} stopOpacity="1" />
            <stop offset="60%" stopColor={colorTo} stopOpacity="0.8" />
            <stop offset="100%" stopColor="transparent" stopOpacity="0" />
          </linearGradient>
        </defs>
        <rect
          x={borderWidth / 2}
          y={borderWidth / 2}
          width={`calc(100% - ${borderWidth}px)`}
          height={`calc(100% - ${borderWidth}px)`}
          rx="12"
          fill="none"
          stroke={`url(#beam-grad-${id})`}
          strokeWidth={borderWidth}
          strokeDasharray="90 320"
          style={{
            animation: `beam-orbit ${duration}s linear infinite`,
          }}
        />
      </svg>
      <style>{`
        @keyframes beam-orbit {
          from { stroke-dashoffset: 410; }
          to { stroke-dashoffset: 0; }
        }
      `}</style>
    </div>
  );
}
