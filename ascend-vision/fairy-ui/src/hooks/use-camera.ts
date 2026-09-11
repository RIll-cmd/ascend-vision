import { useEffect, useState } from 'react';

/** Visibility is deliberately independent from capture lifetime. */
export function useCamera(enabled: boolean) {
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    let owned: MediaStream | undefined;
    setError('');
    void (async () => {
      try {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error('Camera access requires localhost or HTTPS.');
        owned = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' }, audio: false });
        if (disposed) owned.getTracks().forEach(track => track.stop());
        else setStream(owned);
      } catch (err) {
        if (!disposed) setError(err instanceof Error ? err.message : 'Camera unavailable.');
      }
    })();
    return () => { disposed = true; owned?.getTracks().forEach(track => track.stop()); setStream(null); };
  }, [enabled]);
  return { stream, error };
}
