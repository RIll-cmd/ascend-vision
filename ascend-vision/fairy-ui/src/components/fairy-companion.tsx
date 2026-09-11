import { useEffect, useRef, useState } from 'react';
import { Aperture, ArrowUpRight, Video, VideoOff, AudioLines, Headphones, Mic, MicOff, Pause, Play, Radio, ShieldCheck, Sparkles, X } from 'lucide-react';
import { FairyEye, type EyeExpression } from '@/components/ui/fairy-eye';
import { EXPRESSIONS } from '@/lib/eye-motion';
import { Button } from '@/components/ui/button';
import { CameraPreview } from '@/components/ui/camera-preview';
import { StatusBadge } from '@/components/ui/status-badge';
import { DynamicIsland } from '@/components/ui/dynamic-island';
import { useCamera } from '@/hooks/use-camera';
import { useVisionRuntime } from '@/hooks/use-vision-runtime';
import { useFaceTarget } from '@/hooks/use-face-target';

const themes = [
  { name: 'Celestial', base: '#175286', glow: '#58bcff', core: '#e8f5ff' },
  { name: 'Aurora', base: '#155e57', glow: '#66efc4', core: '#e6fff4' },
  { name: 'Solstice', base: '#744021', glow: '#ffba70', core: '#fff3dd' },
];
function formatTime(seconds: number) {
  return `${Math.floor(seconds / 3600).toString().padStart(2, '0')}:${Math.floor(seconds / 60 % 60).toString().padStart(2, '0')}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`;
}

const ALL_EXPRESSIONS: { id: EyeExpression; label: string; icon: string }[] = [
  { id: 'open', label: 'Normal', icon: '✨' },
  { id: 'happy', label: 'Happy', icon: '😊' },
  { id: 'sleepy', label: 'Sleepy', icon: '😴' },
  { id: 'sad', label: 'Sad', icon: '🥺' },
  { id: 'focus', label: 'Focus', icon: '🎯' },
  { id: 'stern', label: 'Stern', icon: '⚡' },
  { id: 'angry', label: 'Angry', icon: '😡' },
  { id: 'curious', label: 'Curious', icon: '🤔' },
  { id: 'surprised', label: 'Surprised', icon: '😲' },
  { id: 'half', label: 'Half', icon: '🌙' },
  { id: 'squint', label: 'Squint', icon: '😑' },
  { id: 'disappointed', label: 'Disappointed', icon: '😞' },
];

/** Integrated Python mode shares capture; standalone mode owns browser media. */
export function FairyCompanion({ runtime = false }: { runtime?: boolean }) {
  const camera = useCamera(!runtime);
  const vision = useVisionRuntime(runtime);
  const browserFace = useFaceTarget(camera.stream);
  const [expression, setExpression] = useState<EyeExpression>('open');
  const [audioReactive, setAudioReactive] = useState(true);
  const [showCamera, setShowCamera] = useState(false);
  const [microphone, setMicrophone] = useState(false);
  const [detected, setDetected] = useState(false);
  const [error, setError] = useState('');
  const [paused, setPaused] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [themeIndex, setThemeIndex] = useState(0);
  const [showSettings, setShowSettings] = useState(false);
  const [sensitivity, setSensitivity] = useState(1.5);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [busy, setBusy] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  const settingsRef = useRef<HTMLDivElement>(null);
  const theme = themes[themeIndex];
  const state = vision.state;
  const faceTarget = runtime ? (vision.connected ? state?.faceTarget : null) : browserFace;
  const isPaused = runtime ? state?.mode !== 'focus' : paused;
  const voiceActive = runtime ? Boolean(state?.voiceEnabled && !state.muted && vision.connected) : (microphone && !error);
  const cameraActive = runtime ? Boolean(state?.cameraReady && vision.connected) : Boolean(camera.stream);
  const speaking = runtime && state?.speaking;
  const currentSeconds = runtime ? (state?.elapsedSeconds ?? 0) : seconds;

  useEffect(() => {
    if (runtime) return;
    const timer = setInterval(() => setSeconds(value => value + 1), 1000);
    return () => clearInterval(timer);
  }, [runtime]);

  // Auto-morph eye expression to match detected emotion or safety triggers
  useEffect(() => {
    if (!runtime || !state) return;
    if (state.phoneBox) {
      setExpression('angry');
    } else if (state.emotion === 'smiling') {
      setExpression('happy');
    } else if (state.emotion === 'fatigue' || (state.fatigue && state.fatigue.includes('fatigue'))) {
      setExpression('sleepy');
    } else if (state.emotion === 'stressed') {
      setExpression('sad');
    } else if (state.posture === 'slouched') {
      setExpression('angry');
    }
  }, [runtime, state?.phoneBox, state?.emotion, state?.fatigue, state?.posture]);

  useEffect(() => {
    if (!showSettings) return;
    const close = (event: PointerEvent) => { if (!settingsRef.current?.contains(event.target as Node)) setShowSettings(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setShowSettings(false); };
    document.addEventListener('pointerdown', close); document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', escape); };
  }, [showSettings]);

  const command = async (value: 'toggle-focus' | 'toggle-voice') => {
    setBusy(true); setError('');
    try {
      const response = await fetch('/api/fairy/command', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command: value }) });
      if (!response.ok) throw new Error('Could not update Vision. Please try again.');
    } catch (err) { setError(err instanceof Error ? err.message : 'Vision is unavailable.'); }
    finally { setBusy(false); }
  };

  const toggleVoice = () => {
    setError('');
    if (runtime) void command('toggle-voice');
    else setMicrophone(value => !value);
  };

  const subtitle = runtime && !vision.connected ? 'Waiting for Vision' : speaking ? 'Fairy is speaking' : detected && voiceActive ? 'I’m listening' : isPaused ? 'Take a moment' : 'Here, with you';
  const activeGlow = EXPRESSIONS[expression]?.palette?.glow ?? theme.glow;

  return <main className="fairy-app" style={{ '--fairy-glow': activeGlow } as React.CSSProperties}>
    <div className="ambient-grid" aria-hidden="true"/>
    <header className="topbar">
      <a className="brand" href="./" aria-label="Fairy home"><span className="brand-mark"><Aperture size={25} strokeWidth={1.25}/></span><span>FAIRY<span className="brand-subtitle">ASCEND VISION</span></span></a>
      <div className="top-status">
        {state?.gesture && (
          <StatusBadge
            variant={state.gesture.includes('Muted') ? 'danger' : 'info'}
            text={state.gesture.toUpperCase()}
          />
        )}
        <span className={`status-dot ${isPaused ? 'dim' : ''}`}/>
        <span>{isPaused ? 'BACKGROUND MODE' : 'FOCUS MODE'}</span>
        <span className="top-divider"/>
        <span className="version">COMPANION / 01</span>
      </div>
    </header>

    <section className="presence" aria-labelledby="presence-title">
      <div className="session-label"><span className="tiny-cross">+</span> YOUR SPACE TO FOCUS <span className="tiny-cross">+</span></div>
      <div className="eye-stage" onPointerMove={event => {
        const box = event.currentTarget.getBoundingClientRect();
        setOffset({ x: (event.clientX - box.left) / box.width * 2 - 1, y: (event.clientY - box.top) / box.height * 2 - 1 });
      }} onPointerLeave={() => setOffset({ x: 0, y: 0 })}>
        <div className="eye-orbit" aria-hidden="true"><i/><i/><i/><i/></div>
        <span className="eye-coordinate left" aria-hidden="true">F / 01</span>
        <FairyEye className="eye-visual" pupilOffset={faceTarget ?? offset} baseColor={theme.base} glowColor={theme.glow} coreColor={theme.core}
          expression={expression} audioReactive={audioReactive} speaking={Boolean(speaking)}
          dilation={speaking ? .25 : 0} enableVoiceControl={!runtime && microphone} voiceSensitivity={sensitivity}
          audioLevel={runtime ? (voiceActive ? (state?.audioLevel ?? 0) * sensitivity / 1.5 : 0) : undefined}
          onVoiceDetected={setDetected} onAudioError={setError} reducedMotion={reducedMotion}/>
        <span className="eye-coordinate right" aria-hidden="true">VISION</span>
        <div className="eye-baseline" aria-hidden="true"/>
      </div>
      <div className="presence-copy">
        <div className="listening-label"><Radio size={12}/><span>{voiceActive ? 'VOICE LINK ACTIVE' : 'QUIET PRESENCE'}</span></div>
        <h1 id="presence-title">{subtitle}<span>.</span></h1>
        <p>{isPaused ? 'Breathe. Your space will be here when you’re ready.' : 'Settle into your work. I’ll keep an eye on the little things.'}</p>
      </div>
      <div className="session-readout"><span>SESSION TIME</span><time>{formatTime(currentSeconds)}</time><span className="readout-line"/><span data-testid="tracking-source">{faceTarget ? 'FACE TRACKING' : 'POINTER TRACKING'}</span></div>
    </section>

    <div className="bottom-area">
      {/* Dynamic Island HUD Capsule */}
      <div style={{ marginBottom: '14px' }}>
        <DynamicIsland
          audioLevel={runtime ? (voiceActive ? (state?.audioLevel ?? 0) : 0) : (detected ? 0.35 : 0)}
          voiceEnabled={voiceActive}
          muted={runtime ? Boolean(state?.muted) : !microphone}
          speaking={Boolean(speaking)}
          lastHeard={state?.lastHeard}
          onToggleMic={toggleVoice}
        />
      </div>

      <div className="expression-controls" role="group" aria-label="Eye expressions">
        {ALL_EXPRESSIONS.map(item => {
          const itemColor = EXPRESSIONS[item.id]?.palette?.glow ?? theme.glow;
          const isSelected = expression === item.id;
          return (
            <Button
              key={item.id}
              variant="ghost"
              aria-pressed={isSelected}
              onClick={() => setExpression(item.id)}
              aria-label={`${item.id} expression`}
              className="flex items-center gap-1.5 text-[10px]"
              style={isSelected ? {
                borderColor: itemColor,
                boxShadow: `0 0 12px -2px ${itemColor}50`,
                color: '#fff',
              } : undefined}
            >
              <span
                aria-hidden="true"
                className="w-2 h-2 rounded-full inline-block shrink-0 transition-transform duration-200"
                style={{
                  backgroundColor: itemColor,
                  boxShadow: `0 0 6px ${itemColor}80`,
                  transform: isSelected ? 'scale(1.2)' : 'scale(1)',
                }}
              />
              <span aria-hidden="true" className="text-xs">{item.icon}</span>
              <span>{item.label}</span>
            </Button>
          );
        })}
        <span className="expression-divider"/>
        <Button variant="ghost" size="icon" aria-label="Audio reactivity" aria-pressed={audioReactive} onClick={() => setAudioReactive(value => !value)}><AudioLines size={16}/></Button>
      </div>

      <div className="controls" aria-label="Companion controls">
        <Button variant={voiceActive ? 'default' : 'outline'} onClick={toggleVoice} aria-label={runtime ? (voiceActive ? 'Mute voice' : 'Unmute voice') : (microphone ? 'Stop microphone' : 'Enable microphone')} aria-pressed={voiceActive} disabled={busy || (runtime && (!vision.connected || !state?.voiceEnabled))}>
          {voiceActive ? <Mic size={16}/> : <MicOff size={16}/>}<span>{runtime ? (voiceActive ? 'Mute voice' : 'Unmute voice') : (microphone ? 'Stop mic' : 'Enable mic')}</span>
        </Button>
        <Button variant="outline" onClick={() => setShowCamera(value => !value)} aria-pressed={showCamera}>{showCamera ? <VideoOff size={16}/> : <Video size={16}/>}<span>{showCamera ? 'Hide camera' : 'Show camera'}</span></Button>
        {runtime && <Button variant="ghost" size="icon" disabled={busy || !vision.connected} aria-label={isPaused ? 'Resume focus' : 'Pause focus'} onClick={() => void command('toggle-focus')}>{isPaused ? <Play size={16}/> : <Pause size={16}/>}</Button>}
        {!runtime && <Button variant="ghost" size="icon" aria-label={paused ? 'Resume focus preview' : 'Pause focus preview'} onClick={() => setPaused(value => !value)}>{paused ? <Play size={16}/> : <Pause size={16}/>}</Button>}
        <div className="settings-anchor" ref={settingsRef}>
          <Button variant="ghost" size="icon" aria-label="Eye appearance" aria-expanded={showSettings} onClick={() => setShowSettings(value => !value)}><Sparkles size={16}/></Button>
          {showSettings && <div className="settings-panel" aria-label="Eye appearance settings"><div className="settings-heading">Make it your own<Button variant="ghost" size="icon" onClick={() => setShowSettings(false)} aria-label="Close appearance settings"><X size={14}/></Button></div>
            <span className="settings-label">LIGHT SPECTRUM</span><div className="theme-options">{themes.map((item, index) => <button key={item.name} aria-label={`${item.name} theme`} aria-pressed={index === themeIndex} onClick={() => setThemeIndex(index)}><i style={{ background: item.glow }}/><span>{item.name}</span></button>)}</div>
            <label className="sensitivity-label" htmlFor="sensitivity">Voice sensitivity <span>{sensitivity.toFixed(1)}×</span></label><input id="sensitivity" type="range" min="0.5" max="3" step="0.1" value={sensitivity} onChange={event => setSensitivity(Number(event.target.value))}/>
            <label className="motion-option"><input type="checkbox" checked={reducedMotion} onChange={event => setReducedMotion(event.target.checked)}/> Reduce eye motion</label>
          </div>}
        </div>
      </div>
      <p className="control-hint"><Headphones size={12}/>{runtime ? 'Your existing Vision listener stays connected.' : 'Enable your microphone and speak to see Fairy respond.'}</p>
      {(error || camera.error) && <p role="alert" className="media-error">{error || `Camera: ${camera.error}`}</p>}
    </div>

    {showCamera && (
      <CameraPreview
        runtime={runtime}
        stream={camera.stream}
        error={camera.error}
        telemetry={state}
        onClose={() => setShowCamera(false)}
      />
    )}

    <footer className="footer">
      <span><ShieldCheck size={13}/> ON-DEVICE PRESENCE</span>
      <span className="camera-status">
        <span className={`status-dot ${cameraActive ? '' : 'dim'}`}/>{cameraActive ? 'Camera active' : 'Camera unavailable'}
        <span className="footer-dot">·</span>{showCamera ? 'Preview visible' : 'Preview hidden'}
      </span>
      <span className="footer-right">A LITTLE MORE FOCUS <ArrowUpRight size={12}/></span>
    </footer>
  </main>;
}
