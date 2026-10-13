import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';

interface Entry { id: number; at: string; description: string; kcal: number; source: string }
interface Food {
  ready: boolean; eaten: number; target: number | null; entries: Entry[];
  waterMl: number; waterTargetMl: number | null; glassMl: number; meals: { name: string; at: string }[];
}

function clock(hhmm: string) {
  const [hour, minute] = hhmm.split(':').map(Number);
  return `${hour % 12 || 12}:${String(minute).padStart(2, '0')} ${hour < 12 ? 'AM' : 'PM'}`;
}

/** Today's food and water from the local diary. Logging also works by saying "I ate ..." in Chat. */
export function FoodPanel() {
  const [food, setFood] = useState<Food | null>(null);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState<{ id: number; kcal: string } | null>(null);

  const call = useCallback(async (path: string, init?: RequestInit) => {
    try {
      const response = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json' } });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'The food diary is unavailable.');
      setError('');
      return body;
    } catch (err) { setError(err instanceof Error ? err.message : 'The food diary is unavailable.'); return null; }
  }, []);
  const load = useCallback(async () => { const body = await call('/api/fairy/food'); if (body) setFood(body); }, [call]);
  const act = async (path: string, init: RequestInit) => { if (await call(path, init)) await load(); };

  useEffect(() => {
    void load();
    const timer = setInterval(() => { if (!document.hidden) void load(); }, 10_000);
    return () => clearInterval(timer);
  }, [load]);

  if (error && !food) return <section className="fairy-data-panel" aria-label="Food and water"><p role="alert" className="media-error">{error}</p></section>;
  if (!food) return <section className="fairy-data-panel" aria-label="Food and water" />;
  if (!food.ready) {
    return <section className="fairy-data-panel" aria-label="Food and water">
      <p className="fairy-chat-empty">No plan yet. In Chat, say “set up my food plan” and answer 8 quick questions.</p>
      <p className="fairy-chat-core-time">You can still log: “I ate …” or “I drank a glass of water”.</p>
    </section>;
  }
  const target = food.target ?? 0;
  const left = target - food.eaten;
  const glasses = Math.floor(food.waterMl / food.glassMl);
  const goalGlasses = Math.floor((food.waterTargetMl ?? 0) / food.glassMl);
  return <section className="fairy-data-panel" aria-label="Food and water">
    {error && <p role="alert" className="media-error">{error}</p>}
    <dl className="fairy-stat-grid">
      <div><dt>Eaten today</dt><dd>{food.eaten.toLocaleString()} kcal</dd></div>
      <div><dt>{left >= 0 ? 'Left' : 'Over'}</dt><dd>{Math.abs(left).toLocaleString()} kcal</dd></div>
      <div><dt>Water</dt><dd>{glasses} / {goalGlasses} glasses</dd></div>
      <div><dt>Daily target</dt><dd>{target.toLocaleString()} kcal</dd></div>
    </dl>
    <div className="fairy-data-toggle">
      <Button variant="outline" onClick={() => void act('/api/fairy/water', { method: 'POST', body: JSON.stringify({ ml: food.glassMl }) })}>+1 glass of water</Button>
    </div>
    <h3>Meal times</h3>
    <p className="fairy-chat-core-time">{food.meals.map(meal => `${meal.name} ${clock(meal.at)}`).join(' · ')}</p>
    <h3>Today</h3>
    {food.entries.length === 0 && <p className="fairy-chat-empty">Nothing logged yet. Say “I ate …” in Chat.</p>}
    <ul className="fairy-memory-list" aria-label="Food logged today">{food.entries.map(entry => <li key={entry.id}>
      <p>{entry.description}{entry.source === 'ai' ? ' (estimate)' : ''}</p>
      {editing?.id === entry.id
        ? <>
            <input aria-label={`Calories for ${entry.description}`} inputMode="numeric" value={editing.kcal}
              onChange={event => setEditing({ id: entry.id, kcal: event.target.value.replace(/\D/g, '').slice(0, 4) })}
              style={{ width: 64 }} />
            <Button variant="outline" onClick={() => { void act(`/api/fairy/food/${entry.id}`, { method: 'PATCH', body: JSON.stringify({ kcal: Number(editing.kcal || 0) }) }); setEditing(null); }}>Save</Button>
          </>
        : <Button variant="ghost" aria-label={`Fix calories for ${entry.description}`} onClick={() => setEditing({ id: entry.id, kcal: String(entry.kcal) })}>{entry.kcal} kcal</Button>}
      <Button variant="ghost" aria-label={`Delete ${entry.description}`} onClick={() => void act(`/api/fairy/food/${entry.id}`, { method: 'DELETE' })}>Delete</Button>
    </li>)}</ul>
    <p className="fairy-chat-core-time">Estimates come from AI and can be off; tap a number to fix it. General guidance, not medical advice.</p>
  </section>;
}
