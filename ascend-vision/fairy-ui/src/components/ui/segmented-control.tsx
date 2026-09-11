"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

export interface SegmentOption<T extends string = string> {
  value: T;
  label: React.ReactNode;
  icon?: React.ReactNode;
}

export interface SegmentedControlProps<T extends string = string> {
  options: SegmentOption<T>[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
  size?: "sm" | "default";
}

export function SegmentedControl<T extends string = string>({
  options,
  value,
  onChange,
  className,
  size = "default",
}: SegmentedControlProps<T>) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const [indicatorStyle, setIndicatorStyle] = React.useState<React.CSSProperties>({});

  React.useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const activeIndex = options.findIndex((opt) => opt.value === value);
    const activeChild = container.children[activeIndex + 1] as HTMLElement | undefined;

    if (activeChild) {
      setIndicatorStyle({
        width: `${activeChild.offsetWidth}px`,
        transform: `translateX(${activeChild.offsetLeft}px)`,
        opacity: 1,
      });
    }
  }, [value, options]);

  return (
    <div
      ref={containerRef}
      role="tablist"
      className={cn(
        "relative inline-flex items-center rounded-lg bg-black/40 border border-white/10 p-0.5 backdrop-blur-md shadow-inner select-none",
        size === "sm" ? "h-7 text-xs" : "h-8 text-xs",
        className
      )}
    >
      {/* Sliding animated background pill */}
      <span
        aria-hidden="true"
        className="pointer-events-none absolute left-0 top-0.5 bottom-0.5 rounded-md bg-white/15 border border-white/20 shadow-sm backdrop-blur-sm transition-all duration-200 ease-out"
        style={indicatorStyle}
      />

      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            role="tab"
            type="button"
            aria-selected={selected}
            onClick={() => onChange(option.value)}
            className={cn(
              "relative z-10 flex items-center justify-center gap-1.5 px-2.5 py-1 font-mono tracking-wide uppercase transition-colors duration-150 rounded-md focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-sky-400",
              selected
                ? "text-white font-medium shadow-[0_0_8px_rgba(255,255,255,0.15)]"
                : "text-slate-400 hover:text-slate-200"
            )}
          >
            {option.icon && <span className="opacity-80">{option.icon}</span>}
            <span>{option.label}</span>
          </button>
        );
      })}
    </div>
  );
}
