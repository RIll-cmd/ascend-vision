# Gap 4: Dormant work revival — Room queue, stale worktrees, archive

**Goal:** "revive" queues the leftover in the Agent Room, stale worktrees count as leftovers, and "archive" puts a
leftover away instead of only muting it.

## Research

- `assistant/revival.py`: finders for branches, Room sessions (from `GET /api/room/sessions`: id, project,
  stage, title, createdAt — no repo or full task), issues. Answers: revive (a promise for tomorrow), later,
  forget. The Room call pattern and `promise_queue` from gap 1 (`integrations/agent_room.py`) apply.
- A Room start may be an issue: `{template, project, repo, issue}` needs no task text. A stopped session's own
  start (task, repo, template, check, project) is in `<room data>/sessions/<id>/state.json`.
- `git worktree list --porcelain` lists worktrees (`worktree`, `branch`, `prunable` lines); the first is the
  main checkout.

## Design

1. `Leftover` gains `repo`, `ref` (branch name, issue number, worktree path) and `start` (a Room start body or
   None), none of them compared.
   - Branch: start = feature session in that repo, task `Finish the work left on branch X (N commits not merged).
     Read git log main..X and git diff main...X first, then carry that work into this session.`; names under
     `archive/` and branches checked out in another worktree are skipped (the worktree item covers them).
   - Issue: start = `{template: feature, project: issue-N, repo, issue: N}`.
   - Room session: only a stopped one gets a start, read from its `state.json` (`task`, `repo.dir`, `template`,
     `check`, `project`); a session waiting at a gate does not.
   - New `left_worktrees(repo, run_git, now)`: non-main worktrees whose last commit is 14–365 days old, or whose
     folder is gone (prunable). Detail: `N files not committed`, `on branch X, nothing uncommitted`, or
     `its folder is gone`. No start.
2. `RevivalCoach(queue=None, archive=None)`:
   - Question: `... Revive it, archive it, later, or forget it?`
   - Revive with a queue and a start: the Room call runs outside the lock; ok → mark revived, reply the Room
     message; failure → reply the message, nothing marked. Otherwise the promise, as today.
   - `archive it` / `archive`: `archive(item) -> (ok, message)` outside the lock; ok → mark `archived` (never
     asked again). No archiver → treated like forget.
   - `drop it` joins forget.
3. `make_archiver(run_git, run_gh)`: branch → `git branch -m X archive/X` (local only, keeps every commit);
   issue → `gh issue close N --reason "not planned"`; Room session and worktree → `(True, "Okay. I'll stop
   bringing it up; I never delete worktrees or sessions.")`.
4. Main: `queue=RoomClient.queue`-based callable, `archive=make_archiver(...)`, Room data path as the other
   helpers.

## Rulings

- "Revive" starts agent work through the Room queue when the Room is on, because the pitch says so and the owner
  answers in words — cost: agent time on a word; a gate-waiting session and a worktree still become a promise.
- A revived branch is a new Room session told to carry the branch's work over; the Room cannot resume an old
  branch — cost: the agents redo the merge.
- Archive renames a branch to `archive/<name>` locally (never deletes, never pushes) and closes an issue as
  "not planned" on GitHub (reopenable) — cost: an issue close is visible to others.
- Vision never removes a worktree or a Room session; archiving those only stops the questions — cost: the owner
  removes them by hand.

## Tasks (TDD)

1. Finders: branch start + archive/ and worktree skips; issue start; stopped Room session start from state.json;
   `left_worktrees` (stale, dirty, prunable, fresh skipped, main skipped).
2. Coach: revive queues (outside lock), failure keeps it; no start → promise; archive ok/failure; drop = forget;
   question wording.
3. `make_archiver` — tests for each kind and a failing git.
4. Wiring, README.
