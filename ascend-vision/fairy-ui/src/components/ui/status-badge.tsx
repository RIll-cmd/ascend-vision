import * as React from "react";
import { cn } from "@/lib/utils";

export type StatusVariant = "good" | "warning" | "danger" | "info" | "neutral";

export interface StatusBadgeProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: StatusVariant;
  dot?: boolean;
  pulse?: boolean;
  label?: string;
  text?: string;
}

const variantStyles: Record<StatusVariant, { badge: string; dot: string }> = {
  good: {
    badge: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20 shadow-[0_0_10px_rgba(16,185,129,0.12)]",
    dot: "bg-emerald-400 shadow-[0_0_6px_#34d399]",
  },
  warning: {
    badge: "bg-amber-500/10 text-amber-300 border-amber-500/20 shadow-[0_0_10px_rgba(245,158,11,0.12)]",
    dot: "bg-amber-400 shadow-[0_0_6px_#fbbf24]",
  },
  danger: {
    badge: "bg-rose-500/10 text-rose-300 border-rose-500/25 shadow-[0_0_10px_rgba(244,63,94,0.15)]",
    dot: "bg-rose-400 shadow-[0_0_6px_#f43f5e]",
  },
  info: {
    badge: "bg-sky-500/10 text-sky-300 border-sky-500/20 shadow-[0_0_10px_rgba(14,165,233,0.12)]",
    dot: "bg-sky-400 shadow-[0_0_6px_#38bdf8]",
  },
  neutral: {
    badge: "bg-white/5 text-slate-300 border-white/10",
    dot: "bg-slate-400",
  },
};

export const StatusBadge = React.forwardRef<HTMLDivElement, StatusBadgeProps>(
  ({ className, variant = "neutral", dot = true, pulse = true, label, text, children, ...props }, ref) => {
    const styles = variantStyles[variant];
    const display = text ?? label ?? children;

    return (
      <div
        ref={ref}
        className={cn(
          "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[9px] font-mono tracking-wider backdrop-blur-md select-none transition-all duration-200",
          styles.badge,
          className
        )}
        {...props}
      >
        {dot && (
          <span className="relative flex h-1.5 w-1.5">
            {pulse && (
              <span
                className={cn(
                  "animate-ping absolute inline-flex h-full w-full rounded-full opacity-75",
                  styles.dot
                )}
              />
            )}
            <span className={cn("relative inline-flex rounded-full h-1.5 w-1.5", styles.dot)} />
          </span>
        )}
        <span>{display}</span>
      </div>
    );
  }
);
StatusBadge.displayName = "StatusBadge";

