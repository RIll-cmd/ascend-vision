"use client";

import * as React from "react";
import { Mic, MicOff, Volume2, Sparkles, MessageSquare } from "lucide-react";
import { cn } from "@/lib/utils";
import { Waveform } from "@/components/ui/waveform";

export interface DynamicIslandProps extends React.HTMLAttributes<HTMLDivElement> {
  active?: boolean;
  level?: number;
  audioLevel?: number;
  voiceEnabled?: boolean;
  muted?: boolean;
  speaking?: boolean;
  isRecording?: boolean;
  isTranscribing?: boolean;
  lastHeard?: string;
  onToggleVoice?: () => void;
  onToggleMic?: () => void;
}

export function DynamicIsland({
  active = true,
  level,
  audioLevel,
  voiceEnabled = true,
  muted = false,
  speaking = false,
  isRecording = false,
  isTranscribing = false,
  lastHeard = "",
  onToggleVoice,
  onToggleMic,
  className,
  ...props
}: DynamicIslandProps) {
  const effectiveLevel = audioLevel ?? level ?? 0;
  const handleToggle = onToggleMic ?? onToggleVoice;
  const [subtitleVisible, setSubtitleVisible] = React.useState(false);
  const [subtitleText, setSubtitleText] = React.useState("");
  const subtitleTimeout = React.useRef<NodeJS.Timeout | null>(null);

  React.useEffect(() => {
    if (lastHeard && lastHeard.trim()) {
      setSubtitleText(lastHeard);
      setSubtitleVisible(true);
      if (subtitleTimeout.current) clearTimeout(subtitleTimeout.current);
      subtitleTimeout.current = setTimeout(() => {
        setSubtitleVisible(false);
      }, 4500);
    }
    return () => {
      if (subtitleTimeout.current) clearTimeout(subtitleTimeout.current);
    };
  }, [lastHeard]);

  const waveformVariant = muted
    ? "muted"
    : isTranscribing
    ? "transcribing"
    : isRecording || effectiveLevel > 0.1
    ? "recording"
    : "default";

  return (
    <div
      data-testid="dynamic-island"
      className={cn(
        "relative flex flex-col items-center justify-center pointer-events-auto select-none",
        className
      )}
      {...props}
    >
      {/* Live Speech Subtitle Bubble (pops up directly above the capsule) */}
      <div
        className={cn(
          "mb-2.5 max-w-md px-3.5 py-1.5 rounded-full text-xs font-mono tracking-wide text-amber-300 bg-slate-950/90 border border-amber-500/30 backdrop-blur-xl shadow-lg shadow-amber-500/5 transition-all duration-300 flex items-center gap-2",
          subtitleVisible
            ? "opacity-100 translate-y-0 scale-100"
            : "opacity-0 translate-y-2 scale-95 pointer-events-none"
        )}
      >
        <MessageSquare size={12} className="text-amber-400 shrink-0" />
        <span className="truncate">
          Heard: <strong className="text-white font-medium">"{subtitleText}"</strong>
        </span>
      </div>

      {/* Main Glassmorphism Dynamic Island Pill */}
      <div className="group relative flex items-center gap-2.5 px-3 py-1.5 rounded-full bg-slate-950/80 hover:bg-slate-950/90 border border-white/12 hover:border-white/20 shadow-2xl backdrop-blur-xl transition-all duration-300">
        {/* Left Action / Mic Status Icon */}
        <button
          type="button"
          onClick={handleToggle}
          disabled={!voiceEnabled}
          className={cn(
            "flex h-7 w-7 items-center justify-center rounded-full transition-all duration-200",
            muted
              ? "bg-rose-500/20 text-rose-300 hover:bg-rose-500/30"
              : isRecording || effectiveLevel > 0.15
              ? "bg-sky-500/30 text-sky-200 shadow-[0_0_12px_rgba(56,189,248,0.4)] animate-pulse"
              : "bg-white/10 text-slate-300 hover:bg-white/15"
          )}
          aria-label={muted ? "Unmute microphone" : "Mute microphone"}
        >
          {muted ? (
            <MicOff size={14} />
          ) : speaking ? (
            <Volume2 size={14} className="text-sky-400 animate-pulse" />
          ) : (
            <Mic size={14} />
          )}
        </button>

        {/* 24-Bar Real-Time Equalizer Waveform */}
        <Waveform
          bars={24}
          level={effectiveLevel}
          active={active && !muted}
          variant={waveformVariant}
          barWidth={2.5}
          maxHeight={22}
          className="px-1"
        />

        {/* Status Indicator Chip */}
        <div className="hidden sm:flex items-center gap-1.5 pl-1 pr-2 text-[11px] font-mono uppercase tracking-wider text-slate-400 border-l border-white/10">
          <span
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              muted
                ? "bg-rose-400"
                : isTranscribing
                ? "bg-amber-400 animate-pulse"
                : speaking
                ? "bg-sky-400 animate-ping"
                : active
                ? "bg-emerald-400 shadow-[0_0_6px_#34d399]"
                : "bg-slate-500"
            )}
          />
          <span>
            {muted
              ? "MUTED"
              : isTranscribing
              ? "TRANSCRIBING"
              : speaking
              ? "SPEAKING"
              : isRecording || effectiveLevel > 0.15
              ? "CAPTURING"
              : "LISTENING"}
          </span>
        </div>
      </div>
    </div>
  );
}
