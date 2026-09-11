import { useEffect, useRef, useState } from 'react';
import { Camera, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { SegmentedControl } from '@/components/ui/segmented-control';
import { TargetingReticle } from '@/components/ui/targeting-reticle';
import { StatusBadge } from '@/components/ui/status-badge';
import { BorderBeam } from '@/components/ui/border-beam';
import type { VisionState } from '@/hooks/use-vision-runtime';

export function CameraPreview({
  stream,
  runtime,
  error,
  telemetry,
  onClose,
}: {
  stream: MediaStream | null;
  runtime: boolean;
  error: string;
  telemetry?: VisionState | null;
  onClose: () => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const image = useRef<HTMLImageElement>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [viewMode, setViewMode] = useState<'clean' | 'hud'>('hud');

  useEffect(() => {
    const element = video.current;
    if (element) element.srcObject = stream;
    return () => {
      if (element) element.srcObject = null;
    };
  }, [stream]);

  useEffect(() => {
    if (!runtime) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let activeUrl: string | undefined;
    const poll = async () => {
      let url: string | undefined;
      try {
        const result = await fetch('/api/fairy/frame.jpg', { signal: controller.signal });
        if (result.status !== 204) {
          if (!result.ok) throw new Error('Preview unavailable');
          const blob = await result.blob();
          if (controller.signal.aborted) return;
          url = URL.createObjectURL(blob);
          const element = image.current;
          if (element) {
            element.src = url;
            await element.decode();
            if (!controller.signal.aborted) {
              setReady(true);
              setFailed(false);
            }
          }
          if (activeUrl) URL.revokeObjectURL(activeUrl);
          activeUrl = url;
          url = undefined;
        }
      } catch {
        if (!controller.signal.aborted) setFailed(true);
      } finally {
        if (url) URL.revokeObjectURL(url);
        if (controller.signal.aborted && activeUrl) URL.revokeObjectURL(activeUrl);
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, 120);
    };
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
      if (activeUrl) URL.revokeObjectURL(activeUrl);
    };
  }, [runtime]);

  const postureState = telemetry?.posture || 'good';
  const isSlouched = postureState === 'slouched';
  const isSlouching = postureState === 'slouching';
  const fatigueState = telemetry?.fatigue || 'alert';
  const isCriticalFatigue = fatigueState === 'critical_drowsy';
  const hasPhone = Boolean(telemetry?.phoneBox);

  return (
    <aside className="camera-card" style={{ width: '310px' }} aria-label="Camera preview">
      <div className="camera-title" style={{ padding: '6px 8px 6px 12px', gap: '8px' }}>
        <span style={{ whiteSpace: 'nowrap' }}>
          <Camera size={13} /> PREVIEW
        </span>
        <SegmentedControl
          options={[
            { value: 'clean', label: 'Clean' },
            { value: 'hud', label: 'AI HUD' },
          ]}
          value={viewMode}
          onChange={(val) => setViewMode(val as 'clean' | 'hud')}
        />
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="Hide camera preview">
          <X size={15} />
        </Button>
      </div>
      <div className="camera-picture">
        {runtime ? (
          <img ref={image} alt="Live local camera feed" hidden={!ready || failed} />
        ) : (
          <video ref={video} autoPlay muted playsInline onLoadedData={() => setReady(true)} />
        )}
        {(!ready || error || failed) && (
          <div className="camera-placeholder">
            <Camera size={24} />
            <p>{error || (failed ? 'Camera preview unavailable.' : 'Connecting to camera…')}</p>
          </div>
        )}

        {viewMode === 'hud' && ready && !failed && (
          <>
            <TargetingReticle
              phoneBox={telemetry?.phoneBox}
              hands={telemetry?.hands}
              eyePoints={telemetry?.eyePoints}
              lipPoints={telemetry?.lipPoints}
              posture={telemetry?.posture}
            />

            {/* Corner telemetry badges */}
            <div className="absolute top-2 left-2 z-10 flex flex-col gap-1 pointer-events-none">
              <StatusBadge
                variant={isSlouched ? 'danger' : isSlouching ? 'warning' : 'good'}
                text={postureState.toUpperCase()}
                pulse={isSlouched}
              />
            </div>

            <div className="absolute top-2 right-2 z-10 flex flex-col items-end gap-1 pointer-events-none">
              <StatusBadge
                variant={isCriticalFatigue ? 'danger' : fatigueState.includes('fatigue') ? 'warning' : 'neutral'}
                text={fatigueState.toUpperCase()}
                pulse={isCriticalFatigue}
              />
            </div>

            {(telemetry?.gesture || telemetry?.emotion) && (
              <div className="absolute bottom-2 left-2 right-2 z-10 flex justify-between items-center pointer-events-none">
                {telemetry?.gesture ? (
                  <StatusBadge variant="info" text={telemetry.gesture.toUpperCase()} />
                ) : <span />}
                {telemetry?.emotion && telemetry.emotion !== 'neutral' && (
                  <StatusBadge
                    variant={telemetry.emotion === 'smiling' ? 'good' : 'warning'}
                    text={telemetry.emotion.toUpperCase()}
                  />
                )}
              </div>
            )}

            {/* Ambient Border Beams */}
            {isSlouched && (
              <BorderBeam colorFrom="#ef4444" colorTo="#b91c1c" size={80} duration={2} borderWidth={2} />
            )}
            {!isSlouched && isSlouching && (
              <BorderBeam colorFrom="#f59e0b" colorTo="#d97706" size={70} duration={3} borderWidth={1.5} />
            )}
            {!isSlouched && !isSlouching && hasPhone && (
              <BorderBeam colorFrom="#10b981" colorTo="#06b6d4" size={60} duration={3.5} borderWidth={1.5} />
            )}
          </>
        )}

        <div className="viewfinder-corner tl" />
        <div className="viewfinder-corner br" />
      </div>
      <p className="camera-caption">
        <span className="status-dot" /> Local preview · {viewMode === 'hud' ? 'Active AI Reticle' : 'Clean Video'}
      </p>
    </aside>
  );
}
