import { useEffect, useRef, useState } from 'react';
import { Video, VideoOff, AudioLines, CircleStop, MessageSquare, Mic, MicOff, Pause, Play, SlidersHorizontal, Volume2, VolumeX, X } from 'lucide-react';
import { FairyEye, type EyeExpression } from '@/components/ui/fairy-eye';
import { EXPRESSIONS } from '@/lib/eye-motion';
import { automaticEyeExpression, resolveEyeMode } from '@/lib/eye-state';
import { Button } from '@/components/ui/button';
import { CameraPreview } from '@/components/ui/camera-preview';
import { CrtSignal, type SignalReason } from '@/components/ui/crt-signal';
import { useCamera } from '@/hooks/use-camera';
import { useVisionRuntime } from '@/hooks/use-vision-runtime';
import { useFaceTarget } from '@/hooks/use-face-target';
import { useLocalChat } from '@/hooks/use-local-chat';
import { useLiveCaption } from '@/hooks/use-live-caption';
import { StatsPanel } from '@/components/stats-panel';
import { MemoryPanel } from '@/components/memory-panel';
import { BrowserPanel } from '@/components/browser-panel';
import { FoodPanel } from '@/components/food-panel';

const themes = [
  { name: 'Celestial', base: '#172660', glow: '#2937d7', core: '#f0f0f6' },
  { name: 'Aurora', base: '#155e57', glow: '#66efc4', core: '#e6fff4' },
  { name: 'Solstice', base: '#744021', glow: '#ffba70', core: '#fff3dd' },
];
function formatTime(seconds: number) {
  return `${Math.floor(seconds / 3600).toString().padStart(2, '0')}:${Math.floor(seconds / 60 % 60).toString().padStart(2, '0')}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`;
}

type Notice = { variant: 'warning' | 'danger' | 'info'; text: string; title?: string };

/** Integrated Python mode shares capture; standalone mode owns browser media. */
export function FairyCompanion({ runtime = false }: { runtime?: boolean }) {
  const camera = useCamera(!runtime);
  const vision = useVisionRuntime(runtime);
  const chat = useLocalChat(runtime);
  const browserFace = useFaceTarget(camera.stream);
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
  const [panelOverride, setPanelOverride] = useState<'chat' | 'stats' | 'memory' | 'browser' | 'food' | 'automation' | 'missions' | 'habits'>('chat');
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
  const audioLevel = runtime ? (state?.audioLevel ?? 0) : (detected ? 0.35 : 0);
  const eyeMode = resolveEyeMode({
    runtime, connected: vision.connected, paused: isPaused,
    speaking: Boolean(speaking), thinking: runtime && chat.status === 'thinking',
    listening: voiceActive && (runtime ? (state?.audioLevel ?? 0) > 0.08 : detected),
  });
  const expression: EyeExpression = runtime ? automaticEyeExpression(state ?? undefined) : 'open';
  const currentSeconds = runtime ? (state?.elapsedSeconds ?? 0) : seconds;
  const caption = useLiveCaption({ events: chat.events, speaking: Boolean(speaking), lastHeard: state?.lastHeard });
  const asleep = runtime && state?.awayState === 'sleeping';
  const signal: SignalReason | null = runtime && !vision.connected ? 'offline' : asleep ? 'sleeping' : null;

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
  const providerName: Record<string, string> = { gemini: 'Gemini', groq: 'Groq', cerebras: 'Cerebras' };
  const keyNotice = ((): Notice | null => {
    const entries = Object.entries(state?.aiKeys ?? {}).filter(([, value]) => value.total > 0);
    const out = entries.find(([, value]) => value.limited >= value.total);
    if (out) {
      const until = out[1].freeAt ? ` until ${new Date(out[1].freeAt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}` : '';
      return { variant: 'danger', text: `${providerName[out[0]] ?? out[0]} keys used up${until}` };
    }
    const low = entries.find(([, value]) => value.limited > 0 && value.total - value.limited <= 1);
    if (low) return { variant: 'warning', text: `${providerName[low[0]] ?? low[0]} keys low · ${low[1].total - low[1].limited} of ${low[1].total} left` };
    return null;
  })();
  // Only problems reach the top bar; everything working stays quiet.
  const notices: Notice[] = [];
  if (runtime && !vision.connected) notices.push({ variant: 'danger', text: aiLabel });
  else {
    if (core?.state === 'offline' || core?.state === 'reauth-required' || core?.state === 'stale') {
      notices.push({ variant: core.state === 'stale' ? 'warning' : 'danger', text: coreLabel,
        title: core.lastCheckedAt ? `Last checked ${new Date(core.lastCheckedAt).toLocaleTimeString()}` : undefined });
    }
    if (ai?.state === 'request-failed' || ai?.state === 'not-configured') notices.push({ variant: 'danger', text: aiLabel });
  }
  if (keyNotice) notices.push({ ...keyNotice, title: 'Your AI keys hit their provider limits. Vision switches to keys that still work.' });
  if (state?.gesture) notices.push({ variant: state.gesture.includes('Muted') ? 'danger' : 'info', text: state.gesture });

  const panelCopy = {
    browser: { title: 'Look it up on the web', detail: '' },
    food: { title: 'Food and water', detail: '' },
    stats: { title: 'Your numbers', detail: '' },
    memory: { title: 'What Vision remembers', detail: '' },
    chat: { title: 'Talk to Vision', detail: 'Type here whenever the microphone is unavailable. Laptop chat can use the current local context.' },
    automation: { title: 'Browser automation', detail: 'Describe the page task in chat. Vision will show a preview and ask before any consequential action.' },
    missions: { title: 'Ascend missions', detail: 'Ask Vision to read your current Core missions. Answers depend on a live Core connection.' },
    habits: { title: 'Habits', detail: 'Use chat to discuss a habit or ask Vision for help. Camera observations alone never mark a habit as failed.' },
  }[selectedPanel];

  const subtitle = {
    offline: 'Waiting for Vision', paused: 'Take a moment', speaking: 'Fairy is speaking',
    thinking: 'Vision is thinking', listening: 'I’m listening', idle: 'Here, with you',
  }[eyeMode];
  const stateLabel = eyeMode === 'offline' ? 'Vision offline' : recovery ? 'Recovery chat'
    : asleep ? 'Sleeping · say "wake up"' : state?.awayState === 'asking' ? 'Are you still there?'
      : isPaused ? 'Paused' : 'Focus mode';
  const activeGlow = EXPRESSIONS[expression]?.palette?.glow ?? theme.glow;
  const meter = Math.round(Math.min(1, Math.max(0, audioLevel)) * 6);

  const captionLine = caption.speaker
    ? <p className={`crt-caption-line ${caption.speaker}`}><span className="crt-speaker">{caption.speaker === 'fairy' ? 'Fairy' : 'You'}</span>
      <span className="crt-words">{caption.text}{caption.typing && <i className="crt-cursor" aria-hidden="true"/>}</span></p>
    : eyeMode === 'thinking' ? <p className="crt-caption-line quiet"><span className="crt-words">thinking<i className="crt-dots" aria-hidden="true"/></span></p>
      : eyeMode === 'paused' ? <p className="crt-caption-line quiet"><span className="crt-words">Paused. Still counting quietly, no roasts.</span></p>
        : state?.awayState === 'asking' ? <p className="crt-caption-line fairy"><span className="crt-speaker">Fairy</span><span className="crt-words">Are you still there?</span></p>
          : null;

  return <main className={`fairy-app ${runtime && showPanel ? 'has-chat' : ''}`} style={{ '--fairy-glow': activeGlow } as React.CSSProperties}>
    <header className="topbar">
      <div className="brand"><span className="brand-mark" aria-hidden="true"><i/></span><span>Fairy</span><span className="brand-subtitle">Ascend Vision</span></div>
      <div className="top-status">
        {notices.map(notice => <span key={notice.text} className={`notice ${notice.variant}`} title={notice.title}>{notice.text}</span>)}
        <span className="state-label"><span className={`status-dot ${eyeMode === 'paused' || eyeMode === 'offline' || asleep ? 'dim' : ''}`}/>{stateLabel}</span>
      </div>
    </header>

    <section className="presence" aria-labelledby="presence-title">
      {recovery && <p role="status" className="control-hint">{state?.runtimeNotice}</p>}
      <div className="crt" data-signal={signal ?? 'on'}>
        <div className="crt-screen">
          <div className="crt-hud top">
            <span className="hud-mode"><i className={`led ${signal || isPaused ? '' : 'on'}`}/>{isPaused ? 'Paused' : 'Focus'}</span>
            <time>{formatTime(currentSeconds)}</time>
            <span className="hud-fill"/>
            <span className="camera-status"><i className={`led ${cameraActive ? 'on' : ''}`}/>{cameraActive ? 'Camera active' : 'Camera unavailable'}</span>
            <span className="mic-status"><span className="meter" aria-hidden="true">{Array.from({ length: 6 }, (_, index) => <i key={index} className={voiceActive && index < meter ? 'lit' : ''}/>)}</span>{voiceActive ? 'Voice link active' : 'Mic off'}</span>
          </div>
          <div className="eye-stage" data-eye-mode={eyeMode} onPointerMove={event => {
            const box = event.currentTarget.getBoundingClientRect();
            setOffset({ x: (event.clientX - box.left) / box.width * 2 - 1, y: (event.clientY - box.top) / box.height * 2 - 1 });
          }} onPointerLeave={() => setOffset({ x: 0, y: 0 })}>
            <FairyEye className="eye-visual" pupilOffset={faceTarget ?? offset} baseColor={theme.base} glowColor={theme.glow} coreColor={theme.core}
              expression={expression} motionMode={eyeMode} audioReactive={audioReactive} speaking={eyeMode === 'speaking'} faceTracking={Boolean(faceTarget)}
              dilation={0} enableVoiceControl={!runtime && microphone} voiceSensitivity={sensitivity}
              audioLevel={runtime ? (eyeMode === 'listening' ? (state?.audioLevel ?? 0) * sensitivity / 1.5 : 0) : undefined}
              onVoiceDetected={setDetected} onAudioError={setError} reducedMotion={reducedMotion}/>
          </div>
          <div className="crt-caption" aria-live="polite">
            <h1 id="presence-title" className="sr-only">{subtitle}.</h1>
            {captionLine}
          </div>
          <div className="crt-hud bottom">
            <span data-testid="tracking-source">{faceTarget ? 'Face tracking' : 'Pointer tracking'}</span>
            <span className="hud-fill"/>
            <span>{showCamera ? 'Preview visible' : 'Preview hidden'}</span>
          </div>
          <div className="crt-glass" aria-hidden="true"/>
          <CrtSignal reason={signal} reducedMotion={reducedMotion}/>
        </div>
        <div className="crt-chin" aria-hidden="true"><span>FAIRY·01</span><span className="hud-fill"/><i className={`power-led ${signal ?? 'on'}`}/></div>
      </div>
    </section>

    <div className="bottom-area">
      <div className="controls" aria-label="Companion controls">
        <Button variant="ghost" className="key" onClick={toggleVoice} aria-label={runtime ? (voiceActive ? 'Pause microphone' : 'Resume microphone') : (microphone ? 'Stop microphone' : 'Enable microphone')} aria-pressed={voiceActive} disabled={recovery || busy || (runtime && !vision.connected)}>
          {voiceActive ? <Mic size={17}/> : <MicOff size={17}/>}<span>Mic</span>
        </Button>
        {runtime && <Button variant="ghost" className="key" onClick={() => void command('toggle-speech')} aria-label={speechMuted ? 'Unmute speech output' : 'Mute speech output'} aria-pressed={!speechMuted} disabled={recovery || busy || !vision.connected}>{speechMuted ? <VolumeX size={17}/> : <Volume2 size={17}/>}<span>Voice</span></Button>}
        {runtime && <Button variant="ghost" className="key" onClick={() => togglePanel('chat')} aria-expanded={showPanel} aria-label="Open chat with Vision"><MessageSquare size={17}/><span>Chat</span></Button>}
        <Button variant="ghost" className="key" disabled={recovery} onClick={() => setShowCamera(value => !value)} aria-pressed={showCamera} aria-label={showCamera ? 'Hide camera' : 'Show camera'}>{showCamera ? <VideoOff size={17}/> : <Video size={17}/>}<span>Cam</span></Button>
        <span className="key-gap" aria-hidden="true"/>
        {runtime && <Button variant="ghost" className="key" disabled={recovery || busy || !vision.connected} aria-label={isPaused ? 'Resume focus' : 'Pause focus'} title={isPaused ? 'Paused: still counting quietly, no roasts' : 'Pause roasts'} onClick={() => void command('toggle-focus')}>{isPaused ? <Play size={17}/> : <Pause size={17}/>}<span>{isPaused ? 'Resume' : 'Pause'}</span></Button>}
        {!runtime && <Button variant="ghost" className="key" aria-label={paused ? 'Resume focus preview' : 'Pause focus preview'} onClick={() => setPaused(value => !value)}>{paused ? <Play size={17}/> : <Pause size={17}/>}<span>{paused ? 'Resume' : 'Pause'}</span></Button>}
        {runtime && <Button variant="ghost" className="key danger" onClick={() => void command('stop-cancel')} aria-label="Stop speech or current browser tasks and pending actions" disabled={recovery || busy || !vision.connected}><CircleStop size={17}/><span>Stop</span></Button>}
        <div className="settings-anchor" ref={settingsRef}>
          <Button variant="ghost" className="key" aria-label="Eye appearance" aria-expanded={showSettings} onClick={() => setShowSettings(value => !value)}><SlidersHorizontal size={17}/><span>Tune</span></Button>
          {showSettings && <div className="settings-panel" aria-label="Eye appearance settings"><div className="settings-heading">Tune the eye<Button variant="ghost" size="icon" onClick={() => setShowSettings(false)} aria-label="Close appearance settings"><X size={14}/></Button></div>
            <span className="settings-label">Colour</span><div className="theme-options">{themes.map((item, index) => <button key={item.name} aria-label={`${item.name} theme`} aria-pressed={index === themeIndex} onClick={() => setThemeIndex(index)}><i style={{ background: item.glow }}/><span>{item.name}</span></button>)}</div>
            <Button variant="ghost" className="settings-toggle" aria-label="Audio reactivity" aria-pressed={audioReactive} onClick={() => setAudioReactive(value => !value)}><AudioLines size={16}/> Audio reactivity</Button>
            <label className="sensitivity-label" htmlFor="sensitivity">Voice sensitivity <span>{sensitivity.toFixed(1)}×</span></label><input id="sensitivity" type="range" min="0.5" max="3" step="0.1" value={sensitivity} onChange={event => setSensitivity(Number(event.target.value))}/>
            <label className="motion-option"><input type="checkbox" checked={reducedMotion} onChange={event => setReducedMotion(event.target.checked)}/> Reduce eye motion</label>
          </div>}
        </div>
      </div>
      {(error || camera.error) && <p role="alert" className="media-error">{error || `Camera: ${camera.error}`}</p>}
    </div>

    {showPanel && runtime && <aside className="fairy-chat-panel" aria-label={panelCopy.title}>
      <header className="fairy-chat-heading">
        <div><span className="chat-kicker">{selectedPanel}</span><h2>{panelCopy.title}</h2></div>
        <Button variant="ghost" size="icon" aria-label="Close chat panel" onClick={() => setShowPanel(false)}><X size={17}/></Button>
      </header>
      <nav className="fairy-panel-nav" aria-label="Vision panels">
        {(['chat', ...(state?.webUse?.enabled ? ['browser'] as const : []), 'food', 'stats', 'memory'] as const).map((panel, index) => <button key={panel} type="button" aria-pressed={panel === selectedPanel} onClick={() => togglePanel(panel)}><span>{index + 1}</span>{panel}</button>)}
      </nav>
      {selectedPanel !== 'chat' && selectedPanel !== 'stats' && selectedPanel !== 'memory' && selectedPanel !== 'browser' && selectedPanel !== 'food' && <section className="fairy-panel-note"><p>{panelCopy.detail}</p><Button variant="outline" onClick={() => { setDraft(selectedPanel === 'missions' ? 'What are my current Ascend missions?' : selectedPanel === 'habits' ? 'Can you help me review my habits?' : 'Help me with a browser task.'); setPanelOverride('chat'); }}>Ask in chat</Button></section>}
      {selectedPanel === 'browser' && <BrowserPanel enabled={Boolean(state?.webUse?.enabled)}/>}
      {selectedPanel === 'food' && <FoodPanel/>}
      {selectedPanel === 'stats' && <StatsPanel/>}
      {selectedPanel === 'memory' && <MemoryPanel/>}
      {selectedPanel !== 'stats' && selectedPanel !== 'memory' && selectedPanel !== 'browser' && selectedPanel !== 'food' && <>
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
      </>}
      <p className="fairy-chat-core-time">{aiLabel}{ai?.lastSuccessAt ? ` · last successful response ${new Date(ai.lastSuccessAt).toLocaleTimeString()}` : ''}</p>
      <p className="fairy-chat-core-time">{coreLabel}{core?.lastCheckedAt ? ` · checked ${new Date(core.lastCheckedAt).toLocaleTimeString()}` : ''}</p>
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
  </main>;
}
