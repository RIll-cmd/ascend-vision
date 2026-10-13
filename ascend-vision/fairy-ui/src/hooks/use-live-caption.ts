import { useEffect, useRef, useState } from 'react';
import type { LocalChatEvent } from '@/hooks/use-local-chat';

/** About how fast Fairy's voice reads text aloud. */
export const CAPTION_CHARS_PER_SECOND = 15;
const HOLD_AFTER_MS = 9_000;
const HEARD_MS = 10_000;
const HISTORY_GRACE_MS = 1_500;
const MAX_CHARS = 110;

export interface LiveCaption {
  speaker: 'fairy' | 'you' | null;
  text: string;
  typing: boolean;
}

function tail(text: string): string {
  if (text.length <= MAX_CHARS) return text;
  const cut = text.slice(-MAX_CHARS);
  const space = cut.indexOf(' ');
  return `…${space > 0 && space < 30 ? cut.slice(space + 1) : cut}`;
}

/** What Fairy is saying (typed out in step with her voice) or what she just heard you say. */
export function useLiveCaption({ events, speaking, lastHeard }: {
  events: LocalChatEvent[]; speaking: boolean; lastHeard?: string;
}): LiveCaption {
  const mountedAt = useRef(Date.now());
  const seen = useRef<number | null>(null);
  const [spoken, setSpoken] = useState<{ text: string; start: number; endedAt: number | null } | null>(null);
  const [heard, setHeard] = useState<{ text: string; at: number } | null>(null);
  const [now, setNow] = useState(Date.now());

  const latest = [...events].reverse().find(event => event.kind === 'assistant' && event.text.trim());
  useEffect(() => {
    if (!latest || latest.cursor === seen.current) return;
    seen.current = latest.cursor;
    // Replies already in the history when the window opened are not "just said".
    if (Date.now() - mountedAt.current < HISTORY_GRACE_MS) return;
    setSpoken({ text: latest.text.trim(), start: Date.now(), endedAt: null });
  }, [latest]);

  useEffect(() => {
    // Voice usually starts a moment after the text arrives: restart the typing with the voice.
    if (speaking) setSpoken(value => value && value.endedAt === null && Date.now() - value.start < 30_000 ? { ...value, start: Date.now() } : value);
    else setSpoken(value => value && value.endedAt === null ? { ...value, endedAt: Date.now() } : value);
  }, [speaking]);

  useEffect(() => {
    const text = lastHeard?.trim();
    if (text) setHeard({ text, at: Date.now() });
  }, [lastHeard]);

  const active = speaking || (spoken !== null && (spoken.endedAt === null || now - spoken.endedAt < HOLD_AFTER_MS))
    || (heard !== null && now - heard.at < HEARD_MS);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 60);
    return () => clearInterval(timer);
  }, [active]);

  const fairyFresh = spoken && (speaking || spoken.endedAt === null || now - spoken.endedAt < HOLD_AFTER_MS);
  const heardFresh = heard && now - heard.at < HEARD_MS;
  if (fairyFresh && (speaking || !heardFresh || heard!.at < spoken!.start)) {
    const count = speaking ? Math.floor((now - spoken!.start) / 1000 * CAPTION_CHARS_PER_SECOND) : spoken!.text.length;
    const shown = spoken!.text.slice(0, Math.max(1, count));
    return { speaker: 'fairy', text: tail(shown), typing: shown.length < spoken!.text.length };
  }
  if (heardFresh) return { speaker: 'you', text: tail(heard!.text), typing: false };
  return { speaker: null, text: '', typing: false };
}
