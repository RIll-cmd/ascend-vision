"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

export interface WaveformProps extends React.HTMLAttributes<HTMLDivElement> {
  bars?: number;
  level?: number;
  waveform?: number[];
  active?: boolean;
  variant?: "default" | "recording" | "transcribing" | "muted";
  barWidth?: number;
  maxHeight?: number;
}

const variantStyles: Record<string, { bar: string; glow: string }> = {
  default: {
    bar: "bg-sky-400",
    glow: "shadow-[0_0_8px_rgba(56,189,248,0.5)]",
  },
  recording: {
    bar: "bg-white",
    glow: "shadow-[0_0_10px_rgba(255,255,255,0.7)]",
  },
  transcribing: {
    bar: "bg-amber-400",
    glow: "shadow-[0_0_8px_rgba(251,191,36,0.6)]",
  },
  muted: {
    bar: "bg-rose-500/70",
    glow: "shadow-[0_0_6px_rgba(244,63,94,0.3)]",
  },
};

export const Waveform = React.forwardRef<HTMLDivElement, WaveformProps>(
  (
    {
      bars = 32,
      level = 0,
      waveform,
      active = true,
      variant = "default",
      barWidth = 2,
      maxHeight = 28,
      className,
      ...props
    },
    ref
  ) => {
    const [heights, setHeights] = React.useState<number[]>(() =>
      Array.from({ length: bars }, () => 3)
    );
    const frameRef = React.useRef(0);

    React.useEffect(() => {
      let rafId: number;

      const animate = () => {
        frameRef.current++;
        const f = frameRef.current;

        if (variant === "muted") {
          setHeights(Array.from({ length: bars }, () => 2));
          return;
        }

        if (variant === "transcribing") {
          // Flowing animated sine pattern for transcription
          setHeights(
            Array.from({ length: bars }, (_, i) => {
              const wave = Math.sin(f * 0.12 + i * 0.4) * 0.5 + 0.5;
              return 4 + wave * (maxHeight - 8);
            })
          );
          rafId = requestAnimationFrame(animate);
          return;
        }

        if (waveform && waveform.length >= bars) {
          const slice = waveform.slice(-bars);
          setHeights(slice.map((v) => Math.max(2, Math.min(maxHeight, v * maxHeight))));
          rafId = requestAnimationFrame(animate);
          return;
        }

        // Generate lively audio bars scaled by level
        const effectiveLevel = active ? Math.max(0.04, level) : 0.04;
        setHeights(
          Array.from({ length: bars }, (_, i) => {
            const w1 = Math.sin(f * 0.08 + i * 0.45) * 0.5 + 0.5;
            const w2 = Math.sin(f * 0.14 - i * 0.3 + 1.2) * 0.3 + 0.3;
            const jitter = (Math.random() - 0.5) * 0.15;
            const combined = Math.max(0, Math.min(1, (w1 * 0.6 + w2 * 0.4 + jitter)));
            const minH = 2;
            const maxH = minH + (maxHeight - minH) * effectiveLevel;
            return minH + (maxH - minH) * combined;
          })
        );

        rafId = requestAnimationFrame(animate);
      };

      rafId = requestAnimationFrame(animate);
      return () => cancelAnimationFrame(rafId);
    }, [bars, level, waveform, active, variant, maxHeight]);

    const style = variantStyles[variant] || variantStyles.default;

    return (
      <div
        ref={ref}
        role="presentation"
        className={cn("flex items-center justify-center gap-[3px] h-8 px-1", className)}
        {...props}
      >
        {heights.map((h, index) => (
          <span
            key={index}
            className={cn(
              "rounded-full transition-[height] duration-75 ease-out",
              style.bar,
              h > 10 ? style.glow : ""
            )}
            style={{
              width: `${barWidth}px`,
              height: `${Math.round(h)}px`,
            }}
          />
        ))}
      </div>
    );
  }
);
Waveform.displayName = "Waveform";
