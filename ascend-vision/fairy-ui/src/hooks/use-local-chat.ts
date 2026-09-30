import { useCallback, useEffect, useRef, useState } from 'react';

export interface LocalChatEvent {
  cursor: number;
  turn_id: string;
  source: string;
  kind: 'user' | 'assistant' | 'status';
  text: string;
  status: string;
  created_at: string;
  reply_source?: 'model' | 'offline' | 'tool' | null;
  provider?: string | null;
  model?: string | null;
  failure_reason?: string | null;
}

export function useLocalChat(enabled: boolean) {
  const [events, setEvents] = useState<LocalChatEvent[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const cursor = useRef(0);
  const session = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const response = await fetch(`/api/fairy/chat/events?after=${cursor.current}`, {
          cache: 'no-store', signal: controller.signal,
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || 'Vision chat is unavailable.');
        const sessionChanged = payload.sessionId !== session.current;
        if (sessionChanged) {
          session.current = payload.sessionId;
          cursor.current = 0;
          if (!controller.signal.aborted) {
            setSessionId(payload.sessionId);
            setEvents([]);
            setStatus('');
          }
        }
        if (Array.isArray(payload.events) && payload.events.length) {
          if (!controller.signal.aborted) {
            setEvents(current => {
              const existing = sessionChanged ? [] : current;
              const seen = new Set(existing.map(event => event.cursor));
              return [...existing, ...payload.events.filter((event: LocalChatEvent) => !seen.has(event.cursor))]
                .slice(-400);
            });
            for (const event of payload.events as LocalChatEvent[]) {
              if (event.kind === 'status') setStatus(event.status === 'error' ? event.text : event.status);
              if (event.kind === 'assistant') setStatus('');
            }
          }
        }
        if (Array.isArray(payload.events) && payload.events.length) {
          cursor.current = payload.events[payload.events.length - 1].cursor;
        }
        if (!controller.signal.aborted) { setConnected(true); setError(''); }
      } catch (caught) {
        if (!controller.signal.aborted) {
          setConnected(false);
          setError(caught instanceof Error ? caught.message : 'Vision chat is unavailable.');
        }
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, 350);
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [enabled]);

  const send = useCallback(async (text: string) => {
    const normalized = text.trim();
    if (!enabled || !normalized || sending) return false;
    setSending(true);
    setError('');
    setStatus('sending');
    try {
      const response = await fetch('/api/fairy/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: normalized }),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.error || 'Message could not be sent.');
      setStatus('queued');
      return true;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Message could not be sent.');
      setStatus('');
      return false;
    } finally {
      setSending(false);
    }
  }, [enabled, sending]);

  return { events, sessionId, connected, sending, error, status, send };
}
