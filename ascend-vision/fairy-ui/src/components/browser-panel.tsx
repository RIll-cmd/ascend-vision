import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';

interface Proposal {
  action_id?: string;
  digest?: string;
  action?: string;
  origin?: string;
  target_label?: string;
  expected_effect?: string;
  arguments?: { value?: string; filename?: string };
}
interface Finding { text?: string; sources?: string[] }
interface TaskResult { findings?: Finding[]; reason?: string }
interface TaskEvent { sequence: number; state?: string; summary?: string; proposal?: Proposal | null }
interface TaskPage { state: string; events: TaskEvent[]; nextCursor: number; resetRequired: boolean; result?: TaskResult | null }

const TERMINAL = ['completed', 'partial', 'failed', 'cancelled', 'unknown'];

function safeLink(source: string) {
  try {
    const url = new URL(source);
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null;
  } catch { return null; }
}

/** Starts a public-web research task in Vision's own Chrome window and shows every step. */
export function BrowserPanel({ enabled }: { enabled: boolean }) {
  const [goal, setGoal] = useState('');
  const [taskId, setTaskId] = useState<string | null>(null);
  const [state, setState] = useState('');
  const [steps, setSteps] = useState<string[]>([]);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [result, setResult] = useState<TaskResult | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const cursor = useRef(0);
  const submissionId = useRef<string | null>(null);

  const request = useCallback(async (path: string, init?: RequestInit) => {
    const response = await fetch(path, init);
    const body = await response.json().catch(() => null);
    if (!response.ok || !body) throw new Error((body && body.error) || 'Web use is unavailable.');
    return body;
  }, []);

  useEffect(() => {
    if (!taskId || TERMINAL.includes(state)) return;
    let alive = true;
    let polling = false;
    const poll = async () => {
      if (polling) return;
      polling = true;
      try {
        const page: TaskPage = await request(`/api/browser/tasks/${encodeURIComponent(taskId)}?after=${cursor.current}`);
        if (!alive) return;
        if (page.resetRequired) setSteps([]);
        const fresh = page.events.map(event => event.summary || event.state || '').filter(Boolean);
        if (fresh.length) setSteps(previous => [...previous, ...fresh]);
        const latest = [...page.events].reverse().find(event => event.proposal);
        if (latest?.proposal) setProposal(latest.proposal);
        cursor.current = page.nextCursor ?? cursor.current;
        setState(page.state);
        setResult(page.result ?? null);
        if (page.state !== 'waiting_for_user') setProposal(null);
        setError('');
      } catch (err) { if (alive) setError(err instanceof Error ? err.message : 'Web use is unavailable.'); }
      finally { polling = false; }
    };
    void poll();
    const timer = setInterval(() => void poll(), 1000);
    return () => { alive = false; clearInterval(timer); };
  }, [taskId, state, request]);

  const start = async (event: React.FormEvent) => {
    event.preventDefault();
    const text = goal.trim();
    if (!text || busy) return;
    setBusy(true); setError('');
    try {
      submissionId.current = submissionId.current ?? crypto.randomUUID().replaceAll('-', '');
      const receipt = await request('/api/browser/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': submissionId.current },
        body: JSON.stringify({ goal: text }),
      });
      submissionId.current = null;
      cursor.current = 0;
      setSteps([]); setResult(null); setProposal(null);
      setTaskId(receipt.taskId); setState(receipt.state); setGoal('');
    } catch (err) { setError(err instanceof Error ? err.message : 'The task could not be started.'); }
    finally { setBusy(false); }
  };

  const control = async (command: 'pause' | 'resume' | 'stop') => {
    if (!taskId) return;
    try {
      const receipt = await request(`/api/browser/tasks/${encodeURIComponent(taskId)}/control`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command }),
      });
      setState(receipt.state);
    } catch (err) { setError(err instanceof Error ? err.message : 'That did not work.'); }
  };

  const decide = async (approved: boolean) => {
    if (!taskId || !proposal) return;
    setBusy(true);
    try {
      const receipt = await request(`/api/browser/tasks/${encodeURIComponent(taskId)}/decision`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ actionId: proposal.action_id, proposalDigest: proposal.digest, approved }),
      });
      setProposal(null); setState(receipt.state);
    } catch (err) { setError(err instanceof Error ? err.message : 'The review was not accepted.'); }
    finally { setBusy(false); }
  };

  if (!enabled) {
    return <section className="fairy-data-panel" aria-label="Web use">
      <p className="fairy-chat-empty">Web use is off. Run Setup.bat again and answer yes to turn it on.</p>
    </section>;
  }
  const active = Boolean(taskId) && !TERMINAL.includes(state);
  const findings = result?.findings ?? [];
  return <section className="fairy-data-panel" aria-label="Web use">
    <form className="fairy-browser-form" onSubmit={start}>
      <textarea value={goal} onChange={event => setGoal(event.target.value)} rows={3} maxLength={4000}
        placeholder="What should I look up? For example: what changed in the latest Playwright release?"
        aria-label="Web task" disabled={busy || active} />
      <Button type="submit" disabled={!goal.trim() || busy || active}>Look it up</Button>
    </form>
    <p className="fairy-chat-core-time">Opens its own Chrome window. It only reads; anything that clicks or types asks you first.</p>
    {error && <p role="alert" className="media-error">{error}</p>}
    {taskId && <p role="status" className="fairy-chat-status">Task {state.replaceAll('_', ' ') || 'starting'}.</p>}
    {active && <div className="fairy-data-toggle">
      {state === 'paused'
        ? <Button variant="ghost" onClick={() => void control('resume')}>Resume</Button>
        : <Button variant="ghost" onClick={() => void control('pause')}>Pause</Button>}
      <Button variant="ghost" onClick={() => void control('stop')}>Stop</Button>
    </div>}
    {proposal && <div className="fairy-browser-review" role="group" aria-label="Approve this action">
      <h3>Approve this action?</h3>
      <p>{proposal.action ?? 'action'} at {proposal.origin ?? 'unknown site'}: {proposal.target_label ?? 'selected control'}.</p>
      {proposal.expected_effect && <p>{proposal.expected_effect}</p>}
      {typeof proposal.arguments?.value === 'string' && <p>Value to enter: {proposal.arguments.value}</p>}
      <div className="fairy-data-toggle">
        <Button variant="outline" disabled={busy} onClick={() => void decide(true)}>Approve</Button>
        <Button variant="ghost" disabled={busy} onClick={() => void decide(false)}>Reject</Button>
      </div>
    </div>}
    {steps.length > 0 && <ol className="fairy-browser-steps" aria-label="Steps so far">{steps.map((step, index) => <li key={index}>{step}</li>)}</ol>}
    {(findings.length > 0 || result?.reason) && <div className="fairy-browser-result" aria-label="Result">
      {findings.map((finding, index) => <div key={index}>
        <p>{finding.text}</p>
        {(finding.sources ?? []).map(source => { const href = safeLink(source); return href ? <a key={href} href={href} target="_blank" rel="noopener noreferrer">{href}</a> : null; })}
      </div>)}
      {!findings.length && result?.reason && <p>{result.reason}</p>}
    </div>}
    {taskId && TERMINAL.includes(state) && <p className="fairy-chat-core-time">Stopping cannot undo something a website already did. Check the site if you approved an action.</p>}
  </section>;
}
