import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';

interface Item { id: number; text: string }
interface Memory { enabled: boolean; pending: Item[]; active: Item[] }

/** Approve or reject what Vision proposes to remember; nothing is used until approved. */
export function MemoryPanel() {
  const [memory, setMemory] = useState<Memory | null>(null);
  const [error, setError] = useState('');

  const call = useCallback(async (path: string, init?: RequestInit) => {
    try {
      const response = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json' } });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'Memory is unavailable.');
      setError('');
      return body;
    } catch (err) { setError(err instanceof Error ? err.message : 'Memory is unavailable.'); return null; }
  }, []);
  const load = useCallback(async () => { const body = await call('/api/fairy/memory'); if (body) setMemory(body); }, [call]);
  const act = async (path: string, init: RequestInit) => { if (await call(path, init)) await load(); };

  useEffect(() => {
    void load();
    const timer = setInterval(() => { if (!document.hidden) void load(); }, 5_000);
    return () => clearInterval(timer);
  }, [load]);

  return <section className="fairy-data-panel" aria-label="Remembered facts">
    {error && <p role="alert" className="media-error">{error}</p>}
    {memory && <>
      <label className="speak-replies-toggle"><input type="checkbox" checked={memory.enabled} onChange={event => void act('/api/fairy/memory/settings', { method: 'PUT', body: JSON.stringify({ enabled: event.target.checked }) })}/> Remember things I ask it to</label>
      <h3>Waiting for approval</h3>
      {memory.pending.length === 0 && <p className="fairy-chat-empty">Say “Remember that…” in chat to add something.</p>}
      <ul className="fairy-memory-list">{memory.pending.map(item => <li key={item.id}><p>{item.text}</p>
        <Button variant="outline" onClick={() => void act(`/api/fairy/memory/proposals/${item.id}/approve`, { method: 'POST' })}>Keep</Button>
        <Button variant="ghost" onClick={() => void act(`/api/fairy/memory/proposals/${item.id}/reject`, { method: 'POST' })}>Skip</Button></li>)}</ul>
      <h3>Kept</h3>
      {memory.active.length === 0 && <p className="fairy-chat-empty">Nothing kept yet.</p>}
      <ul className="fairy-memory-list">{memory.active.map(item => <li key={item.id}><p>{item.text}</p>
        <Button variant="ghost" aria-label={`Forget: ${item.text}`} onClick={() => void act(`/api/fairy/memory/${item.id}`, { method: 'DELETE' })}>Forget</Button></li>)}</ul>
    </>}
  </section>;
}
