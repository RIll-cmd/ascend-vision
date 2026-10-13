import { useEffect, useRef, useState } from 'react';

export type SignalReason = 'sleeping' | 'offline';
type Phase = 'on' | 'leaving' | 'away' | 'returning';

const LEAVE_MS = 1100;
const RETURN_MS = 700;

const copy: Record<SignalReason, { title: string; detail: string }> = {
  sleeping: { title: 'Fairy stepped away', detail: 'Sit down or say “wake up”.' },
  offline: { title: 'No signal', detail: 'Reconnecting to Vision…' },
};

/** Live CRT static on a tiny canvas; one still frame when motion is reduced. */
function Static({ reducedMotion }: { reducedMotion: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const element = canvas.current;
    const context = element?.getContext('2d');
    if (!element || !context) return;
    const image = context.createImageData(element.width, element.height);
    const draw = () => {
      for (let index = 0; index < image.data.length; index += 4) {
        const value = Math.random() * 120;
        image.data[index] = value * 0.55;
        image.data[index + 1] = value * 0.85;
        image.data[index + 2] = value;
        image.data[index + 3] = 255;
      }
      context.putImageData(image, 0, 0);
    };
    draw();
    if (reducedMotion) return;
    const timer = setInterval(draw, 70);
    return () => clearInterval(timer);
  }, [reducedMotion]);
  return <canvas ref={canvas} className="crt-static" width={320} height={256} aria-hidden="true"/>;
}

/**
 * Fairy leaving and coming back, like switching off an old monitor:
 * white flash, magenta and cyan ghosts of the eye, then static. Returning plays it in reverse.
 */
export function CrtSignal({ reason, reducedMotion: reducedSetting = false }: { reason: SignalReason | null; reducedMotion?: boolean }) {
  const reducedMotion = reducedSetting || matchMedia('(prefers-reduced-motion: reduce)').matches;
  const present = reason === null;
  const [phase, setPhase] = useState<Phase>(present ? 'on' : 'away');
  const [shown, setShown] = useState<SignalReason>(reason ?? 'offline');
  const last = useRef(present);

  useEffect(() => { if (reason) setShown(reason); }, [reason]);
  useEffect(() => {
    if (last.current === present) { setPhase(present ? 'on' : 'away'); return; }
    last.current = present;
    const next = present ? 'returning' : 'leaving';
    setPhase(next);
    const timer = setTimeout(() => setPhase(present ? 'on' : 'away'), reducedMotion ? 0 : present ? RETURN_MS : LEAVE_MS);
    return () => clearTimeout(timer);
  }, [present, reducedMotion]);

  if (phase === 'on') return null;
  const text = copy[shown];
  return <div className={`crt-signal ${phase}`} data-phase={phase} role="status" aria-live="polite">
    <Static reducedMotion={reducedMotion}/>
    <div className="crt-ghost" aria-hidden="true"><i/></div>
    <div className="crt-flash" aria-hidden="true"/>
    {(phase === 'away' || phase === 'leaving') && <div className="crt-signal-copy">
      <strong>{text.title}</strong><span>{text.detail}</span>
    </div>}
  </div>;
}
