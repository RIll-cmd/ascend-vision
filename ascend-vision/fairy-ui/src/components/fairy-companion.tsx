import { useEffect, useRef, useState } from 'react';
import { Aperture, ArrowUpRight, Video, VideoOff, AudioLines, CircleStop, Headphones, MessageSquare, Mic, MicOff, Pause, Play, Radio, ShieldCheck, Sparkles, Volume2, VolumeX, X } from 'lucide-react';
import { FairyEye, type EyeExpression } from '@/components/ui/fairy-eye';
import { EXPRESSIONS } from '@/lib/eye-motion';
import { automaticEyeExpression, resolveEyeMode } from '@/lib/eye-state';
import { Button } from '@/components/ui/button';
import { CameraPreview } from '@/components/ui/camera-preview';
import { StatusBadge } from '@/components/ui/status-badge';
import { DynamicIsland } from '@/components/ui/dynamic-island';
import { useCamera } from '@/hooks/use-camera';
import { useVisionRuntime } from '@/hooks/use-vision-runtime';
import { useFaceTarget } from '@/hooks/use-face-target';
import { useLocalChat } from '@/hooks/use-local-chat';

const themes = [
  { name: 'Celestial', base: '#172660', glow: '#2937d7', core: '#f0f0f6' },
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
  const chat = useLocalChat(runtime);
  const browserFace = useFaceTarget(camera.stream);
  const [manualExpression, setManualExpression] = useState<EyeExpression | null>(null);
  const [audioReactive, setAudioReactive] = useState(true);
  const [showCamera, setShowCamera] = useState(false);
  const [microphone, setMicrophone] = useState(false);
  const [detected, setDetected] = useState(false);
  const [error, setError] = useState('');
  const [paused, setPaused] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [themeIndex, setThemeIndex] = useState(0);
  const [showSettings, setShowSettings] = useState(false);
  const [showPanel, setShowPanel] = useState(runtime);
  const [panelOverride, setPanelOverride] = useState<'chat' | 'automation' | 'missions' | 'habits'>('chat');
  const [draft, setDraft] = useState('');
  const [sensitivity, setSensitivity] = useState(1.5);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [busy, setBusy] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  const settingsRef = useRef<HTMLDivElement>(null);
  const theme = themes[themeIndex];
  const state = vision.state;
  const recovery = runtime && state?.runtimeMode === 'recovery-chat';
  const faceTarget = runtime ? (vision.connected ? state?.faceTarget : null) : browserFace;
  const isPaused = runtime ? state?.mode !== 'focus' : paused;
  const voiceActive = runtime ? Boolean(state?.voiceEnabled && vision.connected) : (microphone && !error);
  const speechMuted = runtime ? Boolean(state?.muted) : false;
  const selectedPanel = panelOverride;
  const cameraActive = runtime ? Boolean(state?.cameraReady && vision.connected) : Boolean(camera.stream);
  const speaking = runtime && state?.speaking;
  const eyeMode = resolveEyeMode({
    runtime, connected: vision.connected, paused: isPaused,
    speaking: Boolean(speaking), thinking: runtime && chat.status === 'thinking',
    listening: voiceActive && (runtime ? (state?.audioLevel ?? 0) > 0.08 : detected),
  });
  const expression = manualExpression ?? (runtime ? automaticEyeExpression(state ?? undefined) : 'open');
  const expressionLabel = ALL_EXPRESSIONS.find(item => item.id === expression)?.label ?? 'Normal';
  const currentSeconds = runtime ? (state?.elapsedSeconds ?? 0) : seconds;

  useEffect(() => {
    if (runtime) return;
    const timer = setInterval(() => setSeconds(value => value + 1), 1000);
    return () => clearInterval(timer);
  }, [runtime]);

  useEffect(() => {
    if (!runtime || !state?.activePanel) return;
    setPanelOverride(state.activePanel);
    setShowPanel(true);
  }, [runtime, state?.activePanel, state?.activePanelSequence]);

  useEffect(() => {
    if (!showSettings) return;
    const close = (event: PointerEvent) => { if (!settingsRef.current?.contains(event.target as Node)) setShowSettings(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setShowSettings(false); };
    document.addEventListener('pointerdown', close); document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', escape); };
  }, [showSettings]);

  const command = async (value: 'toggle-focus' | 'toggle-voice' | 'toggle-speech' | 'toggle-chat-speech' | 'stop-cancel') => {
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

  const togglePanel = (panel: typeof panelOverride) => {
    setPanelOverride(panel);
    setShowPanel(true);
  };

  const submitChat = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    if (await chat.send(text)) setDraft('');
  };

  const core = state?.coreConnection;
  const ai = state?.aiStatus;
  const coreVariant = core?.state === 'connected' ? 'good'
    : core?.state === 'connecting' || core?.state === 'stale' ? 'warning'
      : core?.state === 'reauth-required' || core?.state === 'offline' ? 'danger' : 'neutral';
  const coreLabel = core?.state === 'connected' ? 'Core connected'
    : core?.state === 'connecting' ? 'Core connecting'
      : core?.state === 'reauth-required' ? 'Core sign-in required'
        : core?.state === 'offline' ? 'Core offline'
          : core?.state === 'stale' ? 'Core status stale' : 'Core not configured';
  const aiLabel = runtime && !vision.connected ? 'AI status unknown · runtime disconnected'
    : !ai || ai.state === 'unknown' ? 'AI status unknown'
    : ai.state === 'not-configured' ? 'AI not configured'
      : ai.state === 'configured-untested' ? 'AI configured · not tested'
        : ai.state === 'requesting' ? 'AI responding · request in progress'
          : ai.state === 'request-failed' ? `AI request failed${ai.lastFailure ? ` · ${ai.lastFailure.replaceAll('_', ' ')}` : ''}`
            : `AI responding${ai.provider ? ` · ${ai.provider}` : ''}`;
  const aiVariant = runtime && !vision.connected ? 'neutral'
    : ai?.state === 'available' ? 'good'
    : ai?.state === 'request-failed' || ai?.state === 'not-configured' ? 'danger'
      : ai?.state === 'configured-untested' || ai?.state === 'requesting' ? 'warning' : 'neutral';
  const panelCopy = {
    chat: { title: 'Talk to Vision', detail: 'Type here whenever the microphone is unavailable. Laptop chat can use the current local context.' },
    automation: { title: 'Browser automation', detail: 'Describe the page task in chat. Vision will show a preview and ask before any consequential action.' },
    missions: { title: 'Ascend missions', detail: 'Ask Vision to read your current Core missions. Answers depend on a live Core connection.' },
    habits: { title: 'Habits', detail: 'Use chat to discuss a habit or ask Vision for help. Camera observations alone never mark a habit as failed.' },
  }[selectedPanel];

  const subtitle = {
    offline: 'Waiting for Vision', paused: 'Take a moment', speaking: 'Fairy is speaking',
    thinking: 'Vision is thinking', listening: 'I’m listening', idle: 'Here, with you',
  }[eyeMode];
  const activeGlow = EXPRESSIONS[expression]?.palette?.glow ?? theme.glow;

  return <main className={`fairy-app ${runtime && showPanel ? 'has-chat' : ''}`} style={{ '--fairy-glow': activeGlow } as React.CSSProperties}>
    <div className="ambient-grid" aria-hidden="true"/>
    <header className="topbar">
      <a className="brand" href="./" aria-label="Fairy home"><span className="brand-mark"><Aperture size={25} strokeWidth={1.25}/></span><span>FAIRY<span className="brand-subtitle">ASCEND VISION</span></span></a>
      <div className="top-status">
        <StatusBadge variant={coreVariant} text={coreLabel} title={core?.lastCheckedAt ? `Last checked ${new Date(core.lastCheckedAt).toLocaleTimeString()}` : 'Core status has not been checked'} />
        <StatusBadge variant={aiVariant} text={aiLabel} pulse={false} title={ai?.lastSuccessAt ? `Last real model response ${new Date(ai.lastSuccessAt).toLocaleString()}${ai.model ? ` · ${ai.model}` : ''}` : 'AI status is based on provider configuration and actual responses, not Core or microphone status.'} />
        {state?.gesture && (
          <StatusBadge
            variant={state.gesture.includes('Muted') ? 'danger' : 'info'}
            text={state.gesture}
          />
        )}
        <span className={`status-dot ${eyeMode === 'paused' || eyeMode === 'offline' ? 'dim' : ''}`}/>
        <span>{eyeMode === 'offline' ? 'Vision offline' : recovery ? 'Recovery chat' : isPaused ? 'Background mode' : 'Focus mode'}</span>
        <span className="top-divider"/>
        <span className="version">COMPANION / 01</span>
      </div>
    </header>

    <section className="presence" aria-labelledby="presence-title">
      {recovery && <p role="status" className="control-hint">{state?.runtimeNotice}</p>}
      <div className="session-label"><span className="tiny-cross">+</span> Your space to focus <span className="tiny-cross">+</span></div>
      <div className="eye-stage" data-eye-mode={eyeMode} onPointerMove={event => {
        const box = event.currentTarget.getBoundingClientRect();
        setOffset({ x: (event.clientX - box.left) / box.width * 2 - 1, y: (event.clientY - box.top) / box.height * 2 - 1 });
      }} onPointerLeave={() => setOffset({ x: 0, y: 0 })}>
        <div className="eye-orbit" aria-hidden="true"><i/><i/><i/><i/></div>
        <span className="eye-coordinate left" aria-hidden="true">F / 01</span>
        <FairyEye className="eye-visual" pupilOffset={faceTarget ?? offset} baseColor={theme.base} glowColor={theme.glow} coreColor={theme.core}
          expression={expression} motionMode={eyeMode} audioReactive={audioReactive} speaking={eyeMode === 'speaking'} faceTracking={Boolean(faceTarget)}
          dilation={0} enableVoiceControl={!runtime && microphone} voiceSensitivity={sensitivity}
          audioLevel={runtime ? (eyeMode === 'listening' ? (state?.audioLevel ?? 0) * sensitivity / 1.5 : 0) : undefined}
          onVoiceDetected={setDetected} onAudioError={setError} reducedMotion={reducedMotion}/>
        <span className="eye-coordinate right" aria-hidden="true">VISION</span>
        <div className="eye-baseline" aria-hidden="true"/>
      </div>
      <div className="presence-copy">
        <div className="listening-label"><Radio size={12}/><span>{voiceActive ? 'Voice link active' : 'Quiet presence'}</span></div>
        <h1 id="presence-title">{subtitle}<span>.</span></h1>
        <p>{eyeMode === 'paused' ? 'Breathe. Your space will be here when you’re ready.' : eyeMode === 'offline' ? 'Vision is reconnecting to your local session.' : 'Settle into your work. I’ll keep an eye on the little things.'}</p>
      </div>
      <div className="session-readout"><span>Session time</span><time>{formatTime(currentSeconds)}</time><span className="readout-line"/><span data-testid="tracking-source">{faceTarget ? 'Face tracking' : 'Pointer tracking'}</span></div>
    </section>

    <div className="bottom-area">
      {/* Dynamic Island HUD Capsule */}
      <div style={{ marginBottom: '14px' }}>
        <DynamicIsland
          audioLevel={runtime ? (eyeMode === 'listening' ? (state?.audioLevel ?? 0) : 0) : (eyeMode === 'listening' ? 0.35 : 0)}
          voiceEnabled={voiceActive}
          muted={!voiceActive}
          speaking={eyeMode === 'speaking'}
          lastHeard={state?.lastHeard}
          onToggleMic={toggleVoice}
        />
      </div>

      <p aria-live="polite" className="control-hint">Eye expression: {manualExpression ? 'Manual' : 'Automatic'} · {expressionLabel}</p>

      <div className="controls" aria-label="Companion controls">
        <Button variant={voiceActive ? 'default' : 'outline'} onClick={toggleVoice} aria-label={runtime ? (voiceActive ? 'Pause microphone' : 'Resume microphone') : (microphone ? 'Stop microphone' : 'Enable microphone')} aria-pressed={voiceActive} disabled={recovery || busy || (runtime && !vision.connected)}>
          {voiceActive ? <Mic size={16}/> : <MicOff size={16}/>}<span>{runtime ? (voiceActive ? 'Pause mic' : 'Resume mic') : (microphone ? 'Stop mic' : 'Enable mic')}</span>
        </Button>
        {runtime && <Button variant="outline" onClick={() => void command('toggle-speech')} aria-label={speechMuted ? 'Unmute speech output' : 'Mute speech output'} aria-pressed={!speechMuted} disabled={recovery || busy || !vision.connected}>{speechMuted ? <VolumeX size={16}/> : <Volume2 size={16}/>}<span>{speechMuted ? 'Unmute speech' : 'Mute speech'}</span></Button>}
        {runtime && <Button variant="outline" onClick={() => togglePanel('chat')} aria-expanded={showPanel} aria-label="Open chat with Vision"><MessageSquare size={16}/><span>Chat</span></Button>}
        {runtime && <Button variant="ghost" size="icon" onClick={() => void command('stop-cancel')} aria-label="Stop speech or current browser tasks and pending actions" disabled={recovery || busy || !vision.connected}><CircleStop size={16}/></Button>}
        <Button variant="outline" disabled={recovery} onClick={() => setShowCamera(value => !value)} aria-pressed={showCamera}>{showCamera ? <VideoOff size={16}/> : <Video size={16}/>}<span>{showCamera ? 'Hide camera' : 'Show camera'}</span></Button>
        {runtime && <Button variant="ghost" size="icon" disabled={recovery || busy || !vision.connected} aria-label={isPaused ? 'Resume focus' : 'Pause focus'} onClick={() => void command('toggle-focus')}>{isPaused ? <Play size={16}/> : <Pause size={16}/>}</Button>}
        {!runtime && <Button variant="ghost" size="icon" aria-label={paused ? 'Resume focus preview' : 'Pause focus preview'} onClick={() => setPaused(value => !value)}>{paused ? <Play size={16}/> : <Pause size={16}/>}</Button>}
        <div className="settings-anchor" ref={settingsRef}>
          <Button variant="ghost" size="icon" aria-label="Eye appearance" aria-expanded={showSettings} onClick={() => setShowSettings(value => !value)}><Sparkles size={16}/></Button>
          {showSettings && <div className="settings-panel" aria-label="Eye appearance settings" style={{ maxHeight: 'min(70svh, 440px)', overflowY: 'auto' }}><div className="settings-heading">Make it your own<Button variant="ghost" size="icon" onClick={() => setShowSettings(false)} aria-label="Close appearance settings"><X size={14}/></Button></div>
            <span className="settings-label">LIGHT SPECTRUM</span><div className="theme-options">{themes.map((item, index) => <button key={item.name} aria-label={`${item.name} theme`} aria-pressed={index === themeIndex} onClick={() => setThemeIndex(index)}><i style={{ background: item.glow }}/><span>{item.name}</span></button>)}</div>
            <span className="settings-label">EXPRESSION</span>
            <div role="group" aria-label="Eye expressions" style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 4, margin: '10px 0 16px' }}>
              <Button variant="ghost" aria-label="Automatic expression" aria-pressed={manualExpression === null} onClick={() => setManualExpression(null)}>✨ Automatic</Button>
              {ALL_EXPRESSIONS.map(item => <Button key={item.id} variant="ghost" aria-label={`${item.id} expression`} aria-pressed={manualExpression === item.id} onClick={() => setManualExpression(item.id)} title={item.label}>{item.icon} {item.label}</Button>)}
            </div>
            <Button variant="ghost" aria-label="Audio reactivity" aria-pressed={audioReactive} onClick={() => setAudioReactive(value => !value)}><AudioLines size={16}/> Audio reactivity</Button>
            <label className="sensitivity-label" htmlFor="sensitivity">Voice sensitivity <span>{sensitivity.toFixed(1)}×</span></label><input id="sensitivity" type="range" min="0.5" max="3" step="0.1" value={sensitivity} onChange={event => setSensitivity(Number(event.target.value))}/>
            <label className="motion-option"><input type="checkbox" checked={reducedMotion} onChange={event => setReducedMotion(event.target.checked)}/> Reduce eye motion</label>
          </div>}
        </div>
      </div>
      <p className="control-hint"><Headphones size={12}/>{runtime ? 'Your existing Vision listener stays connected.' : 'Enable your microphone and speak to see Fairy respond.'}</p>
      {(error || camera.error) && <p role="alert" className="media-error">{error || `Camera: ${camera.error}`}</p>}
    </div>

    {showPanel && runtime && <aside className="fairy-chat-panel" aria-label={panelCopy.title}>
      <header className="fairy-chat-heading">
        <div><span className="chat-kicker">Local session · {selectedPanel}</span><h2>{panelCopy.title}</h2></div>
        <Button variant="ghost" size="icon" aria-label="Close chat panel" onClick={() => setShowPanel(false)}><X size={17}/></Button>
      </header>
      <nav className="fairy-panel-nav" aria-label="Vision panels">
        {(['chat', 'automation', 'missions', 'habits'] as const).map((panel, index) => <button key={panel} type="button" aria-pressed={panel === selectedPanel} onClick={() => togglePanel(panel)}><span>{index + 1}</span>{panel}</button>)}
      </nav>
      {selectedPanel !== 'chat' && <section className="fairy-panel-note"><p>{panelCopy.detail}</p><Button variant="outline" onClick={() => { setDraft(selectedPanel === 'missions' ? 'What are my current Ascend missions?' : selectedPanel === 'habits' ? 'Can you help me review my habits?' : 'Help me with a browser task.'); setPanelOverride('chat'); }}>Ask in chat</Button></section>}
      <div className="fairy-chat-events" aria-live="polite" aria-relevant="additions text">
        {chat.events.filter(event => event.kind !== 'status').map(event => <article key={event.cursor} className={`fairy-chat-message ${event.kind}`}>
          <span>{event.kind === 'user' ? (event.source === 'voice' ? 'YOU · VOICE' : 'YOU') : `VISION · ${event.reply_source === 'model' ? `AI${event.provider ? ` / ${event.provider}` : ''}` : event.reply_source === 'tool' ? 'LOCAL TOOL' : event.reply_source === 'offline' ? 'OFFLINE RESPONSE' : 'SOURCE UNKNOWN'}`}</span><p>{event.text}</p>
        </article>)}
        {!chat.events.some(event => event.kind !== 'status') && <p className="fairy-chat-empty">{chat.connected ? 'Your local conversation starts here.' : 'Connecting to the Vision runtime…'}</p>}
        {chat.status === 'thinking' && <p className="fairy-chat-status">Vision is thinking…</p>}
      </div>
      <form className="fairy-chat-compose" onSubmit={submitChat}>
        <label className="speak-replies-toggle"><input type="checkbox" checked={Boolean(state?.speakReplies)} onChange={() => void command('toggle-chat-speech')} disabled={busy || !vision.connected}/> Speak typed replies</label>
        <textarea value={draft} onChange={event => setDraft(event.target.value)} maxLength={4000} rows={2} placeholder="Message Vision…" aria-label="Message Vision" disabled={!vision.connected || !chat.sessionId} />
        <div className="fairy-chat-compose-footer"><span role="status">{chat.error || chat.status || (speechMuted ? 'Speech output muted; replies stay visible.' : 'Typed chat works without the microphone.')}</span><Button type="submit" disabled={!draft.trim() || chat.sending || !vision.connected || !chat.sessionId}>{chat.sending ? 'Sending…' : 'Send'}</Button></div>
      </form>
      <p className="fairy-chat-core-time">{aiLabel}{ai?.lastSuccessAt ? ` · last successful response ${new Date(ai.lastSuccessAt).toLocaleTimeString()}` : ''}</p>
      {core?.lastCheckedAt && <p className="fairy-chat-core-time">{coreLabel} · checked {new Date(core.lastCheckedAt).toLocaleTimeString()}</p>}
    </aside>}

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
      <span><ShieldCheck size={13}/> On-device presence</span>
      <span className="camera-status">
        <span className={`status-dot ${cameraActive ? '' : 'dim'}`}/>{cameraActive ? 'Camera active' : 'Camera unavailable'}
        <span className="footer-dot">·</span>{showCamera ? 'Preview visible' : 'Preview hidden'}
      </span>
      <span className="footer-right">A little more focus <ArrowUpRight size={12}/></span>
    </footer>
  </main>;
}
