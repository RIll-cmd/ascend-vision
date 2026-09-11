import { useEffect, useState } from 'react';
import type { Point } from '@/lib/eye-motion';

interface FaceBox { boundingBox: { x: number; y: number; width: number; height: number } }
interface Detector { detect(source: HTMLVideoElement): Promise<FaceBox[]>; close?: () => void }
type DetectorConstructor = new (options: { fastMode: boolean; maxDetectedFaces: number }) => Detector;

async function createDetector(preferNative = true): Promise<Detector> {
  const Native = (window as Window & { FaceDetector?: DetectorConstructor }).FaceDetector;
  if (preferNative && Native) { try { return new Native({ fastMode: true, maxDetectedFaces: 1 }); } catch { /* use local BlazeFace */ } }
  const { FaceDetector, FilesetResolver } = await import('@mediapipe/tasks-vision');
  const files = await FilesetResolver.forVisionTasks(new URL('vision/wasm', document.baseURI).href);
  const detector = await FaceDetector.createFromOptions(files, {
    baseOptions: { modelAssetPath: new URL('vision/blaze_face_short_range.tflite', document.baseURI).href, delegate: 'CPU' },
    runningMode: 'VIDEO', minDetectionConfidence: .6,
  });
  return {
    detect: async source => detector.detectForVideo(source, performance.now()).detections.flatMap(face => {
      const box = face.boundingBox;
      return box ? [{ boundingBox: { x: box.originX, y: box.originY, width: box.width, height: box.height } }] : [];
    }),
    close: () => detector.close(),
  };
}

/** Uses the existing stream, never owns/stops its tracks. Detection is throttled to 10 Hz. */
export function useFaceTarget(stream: MediaStream | null) {
  const [target, setTarget] = useState<Point | null>(null);
  useEffect(() => {
    setTarget(null);
    if (!stream) return;
    const video = document.createElement('video');
    video.muted = true; video.autoplay = true; video.playsInline = true;
    video.srcObject = stream;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let detector: Detector | undefined;
    let retriedWithLocal = false;
    const detect = async () => {
      if (stopped) return;
      try {
        if (video.readyState >= 2 && video.videoWidth > 0 && stream.getVideoTracks().some(track => track.readyState === 'live')) {
          const faces = await detector!.detect(video);
          if (stopped) return;
          const face = faces[0]?.boundingBox;
          setTarget(face ? {
            // Mirror horizontal movement to match the mirrored camera preview.
            x: (1 - (face.x + face.width / 2) / video.videoWidth * 2) * 1.8,
            y: ((face.y + face.height / 2) / video.videoHeight * 2 - 1) * 1.8,
          } : null);
        } else setTarget(null);
      } catch {
        if (!stopped) setTarget(null);
        if (!stopped && !retriedWithLocal) {
          retriedWithLocal = true;
          try {
            detector?.close?.();
            detector = await createDetector(false);
            if (stopped) detector.close?.();
          } catch { /* Keep pointer tracking if the local detector cannot initialize. */ }
        }
      }
      if (!stopped) timer = setTimeout(detect, 100);
    };
    void video.play().then(async () => {
      if (stopped) return;
      detector = await createDetector();
      if (stopped) { detector.close?.(); return; }
      await detect();
    }).catch(() => { if (!stopped) setTarget(null); });
    return () => { stopped = true; clearTimeout(timer); detector?.close?.(); video.pause(); video.srcObject = null; };
  }, [stream]);
  return target;
}
