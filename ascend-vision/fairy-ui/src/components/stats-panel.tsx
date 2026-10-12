import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';

interface Day { date: string; pickups: number; focus: number; background: number }
interface Stats {
  database_exists: boolean;
  summary: { pickups: number; phone_seconds: number; focus_seconds: number; longest_streak_seconds: number };
  days: Day[];
  distractions?: { site: string; seconds: number }[] | null;
}

function duration(seconds: number) {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/** Read-only habit numbers from the local database; an error never shows as zero. */
export function StatsPanel() {
  const [days, setDays] = useState<7 | 28>(7);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const response = await fetch(`/api/fairy/stats?days=${days}`);
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || 'Statistics are unavailable.');
        if (alive) { setStats(body); setError(''); }
      } catch (err) { if (alive) setError(err instanceof Error ? err.message : 'Statistics are unavailable.'); }
    };
    void load();
    const timer = setInterval(() => { if (!document.hidden) void load(); }, 10_000);
    return () => { alive = false; clearInterval(timer); };
  }, [days]);

  const peak = Math.max(1, ...(stats?.days.map(day => day.pickups) ?? [1]));
  return <section className="fairy-data-panel" aria-label="Pickup numbers">
    <div className="fairy-data-toggle" role="group" aria-label="Date range">
      {([7, 28] as const).map(value => <Button key={value} variant="ghost" aria-pressed={days === value} onClick={() => setDays(value)}>{value} days</Button>)}
    </div>
    {error && <p role="alert" className="media-error">{error}</p>}
    {stats && !error && <>
      {!stats.database_exists && <p className="fairy-chat-empty">No history yet. Your first focus session will appear here.</p>}
      <dl className="fairy-stat-grid">
        <div><dt>Phone pickups</dt><dd>{stats.summary.pickups}</dd></div>
        <div><dt>Phone time</dt><dd>{duration(stats.summary.phone_seconds)}</dd></div>
        <div><dt>Focus time</dt><dd>{duration(stats.summary.focus_seconds)}</dd></div>
        <div><dt>Longest phone-free</dt><dd>{duration(stats.summary.longest_streak_seconds)}</dd></div>
      </dl>
      <ol className="fairy-bars" aria-label="Pickups per day">
        {stats.days.map(day => <li key={day.date} title={`${day.date}: ${day.pickups} pickups`}>
          <i style={{ height: `${Math.max(day.pickups ? 6 : 2, day.pickups / peak * 100)}%` }}/>
          <span>{day.pickups}</span>
        </li>)}
      </ol>
      <h3>Distracting sites during focus</h3>
      {stats.distractions == null
        ? <p className="fairy-chat-empty">Site times are unavailable right now.</p>
        : stats.distractions.length === 0
          ? <p className="fairy-chat-empty">None in this period. Nice.</p>
          : <ul className="fairy-memory-list" aria-label="Time on distracting sites">{stats.distractions.map(item =>
              <li key={item.site}><p>{item.site}</p><strong>{duration(item.seconds)}</strong></li>)}</ul>}
      <p className="fairy-chat-core-time">Counts are camera estimates, not a guarantee of grip. Site times come from browser tab names; pages are never saved.</p>
    </>}
  </section>;
}
