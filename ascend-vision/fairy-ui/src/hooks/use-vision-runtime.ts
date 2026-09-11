import { useEffect, useState } from 'react';

export interface PhoneBox {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  confidence: number;
  posture?: string | null;
}

export interface HandLandmark {
  points: [number, number][];
}

export interface VisionState {
  mode: string;
  cameraReady: boolean;
  audioLevel: number;
  voiceEnabled: boolean;
  muted: boolean;
  speaking: boolean;
  elapsedSeconds: number;
  faceTarget?: { x: number; y: number } | null;
  phoneBox?: PhoneBox | null;
  hands?: HandLandmark[];
  eyePoints?: [number, number][];
  lipPoints?: [number, number][];
  posture?: string | null;
  fatigue?: string | null;
  gesture?: string | null;
  emotion?: 'neutral' | 'smiling' | 'fatigue' | 'stressed' | string;
  lastHeard?: string;
}
export function useVisionRuntime(enabled: boolean) {
  const [state, setState] = useState<VisionState | null>(null);
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const response = await fetch('/api/fairy/state', { signal: controller.signal });
        if (!response.ok) throw new Error('Runtime unavailable');
        const next: VisionState = await response.json();
        if (!controller.signal.aborted) { setState(next); setConnected(true); }
      } catch { if (!controller.signal.aborted) setConnected(false); }
      if (!controller.signal.aborted) timer = setTimeout(poll, 150);
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [enabled]);
  return { state, connected };
}
