import { useEffect, useRef, useState } from 'react';

/** One audio owner per mount. Late permission responses also release their tracks. */
export function useMicrophone(enabled: boolean, sensitivity = 1.5) {
  const level = useRef(0);
  const [status, setStatus] = useState<'off' | 'requesting' | 'ready' | 'error'>('off');
  const [error, setError] = useState('');
  useEffect(() => {
    if (!enabled) { level.current = 0; setStatus('off'); return; }
    let disposed = false;
    let stream: MediaStream | undefined;
    let context: AudioContext | undefined;
    let source: MediaStreamAudioSourceNode | undefined;
    let analyser: AnalyserNode | undefined;
    let frame = 0;
    setStatus('requesting'); setError('');
    const release = () => {
      cancelAnimationFrame(frame);
      stream?.getTracks().forEach(track => track.stop());
      source?.disconnect(); analyser?.disconnect();
      if (context && context.state !== 'closed') void context.close().catch(() => {});
      level.current = 0;
    };
    void (async () => {
      try {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error('Microphone access requires localhost or HTTPS.');
        stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false } });
        if (disposed) { release(); return; }
        context = new AudioContext();
        await context.resume();
        if (disposed) { release(); return; }
        source = context.createMediaStreamSource(stream);
        analyser = context.createAnalyser(); analyser.fftSize = 512;
        source.connect(analyser);
        const samples = new Float32Array(analyser.fftSize);
        const sample = () => {
          if (disposed || !analyser) return;
          analyser.getFloatTimeDomainData(samples);
          let sum = 0;
          for (const value of samples) sum += value * value;
          level.current = Math.min(1, Math.sqrt(sum / samples.length) * sensitivity * 3);
          frame = requestAnimationFrame(sample);
        };
        setStatus('ready'); sample();
      } catch (err) {
        release();
        if (!disposed) { setStatus('error'); setError(err instanceof Error ? err.message : 'Microphone unavailable.'); }
      }
    })();
    return () => { disposed = true; release(); };
  }, [enabled, sensitivity]);
  return { level, status, error };
}
