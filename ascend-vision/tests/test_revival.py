from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from assistant.revival import (RevivalCoach, RevivalStore, default_repos, left_branches, left_issues,
                               left_room_sessions, scan)

NOW = datetime(2026, 10, 30, 11, 0)
REPO = Path('D:/ascend_hub')


def ago(days):
    return int((NOW - timedelta(days=days)).timestamp())


def git_for(branches, ahead, default='main', origin_head=None):
    def run_git(repo, *args):
        if args[:2] == ('symbolic-ref', '--short'):
            return f'origin/{origin_head}\n' if origin_head else None
        if args[:3] == ('rev-parse', '--verify', '--quiet'):
            return 'abc123\n' if args[3] == f'refs/heads/{default}' else None
        if args[0] == 'for-each-ref':
            return ''.join(f'{name}\t{stamp}\n' for name, stamp in branches)
        if args[0] == 'rev-list':
            # commits already on the default branch by rebase or cherry-pick must not count
            assert {'--cherry-pick', '--right-only', '--no-merges'} <= set(args), args
            return f"{ahead[args[-2].split('...')[1]]}\n"
        raise AssertionError(args)
    return run_git


def test_branches_left_two_weeks_to_a_year_with_unmerged_commits():
    run_git = git_for([('main', ago(1)), ('claude/fix-login', ago(21)), ('backup/old-wip', ago(400)),
                       ('feat/fresh', ago(3)), ('merged-thing', ago(30))],
                      {'claude/fix-login': 4, 'merged-thing': 0})
    (found,) = left_branches(REPO, run_git, NOW)
    assert (found.key, found.label, found.detail) == (
        'branch:D:/ascend_hub:claude/fix-login', 'branch claude fix login in ascend hub', '4 commits not merged')
    assert found.last == NOW - timedelta(days=21)


def test_a_repo_without_main_or_master_or_failing_git_is_skipped():
    assert left_branches(REPO, git_for([('x', ago(20))], {'x': 1}, default='trunk'), NOW) == []
    assert left_branches(REPO, lambda repo, *args: None, NOW) == []
    (found,) = left_branches(REPO, git_for([('trunk', ago(1)), ('x', ago(20))], {'x': 1}, default='trunk',
                                           origin_head='trunk'), NOW)
    assert found.detail == '1 commit not merged'


def test_room_sessions_stopped_or_waiting_at_a_gate():
    stamp = lambda days: (NOW - timedelta(days=days)).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    payload = {'sessions': [
        {'id': 'a', 'stage': 'stopped', 'title': 'Add dark mode', 'createdAt': stamp(5)},
        {'id': 'b', 'stage': 'gate-plan', 'title': 'Fix the queue', 'createdAt': stamp(10)},
        {'id': 'c', 'stage': 'done', 'title': 'Done thing', 'createdAt': stamp(10)},
        {'id': 'd', 'stage': 'stopped', 'title': 'Too new', 'createdAt': stamp(1)},
        {'id': 'e', 'stage': 'stopped', 'title': 'Bad date', 'createdAt': 'soon'}]}
    found = left_room_sessions(payload, NOW)
    assert [(f.key, f.label, f.detail) for f in found] == [
        ('room:a', 'the Agent Room session Add dark mode', 'You stopped it'),
        ('room:b', 'the Agent Room session Fix the queue', 'It is waiting for you at the plan gate')]
    assert left_room_sessions(None, NOW) == [] and left_room_sessions({'sessions': 'x'}, NOW) == []


def test_issues_the_owner_opened_and_nobody_touched_for_a_month():
    def run_gh(repo, *args):
        return json.dumps([{'number': 12, 'title': 'Crash on start', 'updatedAt': '2026-09-01T10:00:00Z'},
                           {'number': 13, 'title': 'Fresh', 'updatedAt': '2026-10-29T10:00:00Z'}])
    (found,) = left_issues(REPO, run_gh, NOW)
    assert (found.key, found.label) == ('issue:D:/ascend_hub:12', 'issue 12 in ascend hub, Crash on start')
    assert left_issues(REPO, lambda repo, *args: None, NOW) == []
    assert left_issues(REPO, lambda repo, *args: 'not json', NOW) == []


def test_default_repos_are_the_git_folders_next_to_visions(tmp_path):
    for name in ('ascend-vision', 'ascend_hub', 'notes'):
        (tmp_path / name).mkdir()
    (tmp_path / 'ascend-vision' / '.git').mkdir()
    (tmp_path / 'ascend_hub' / '.git').mkdir()
    assert default_repos(tmp_path / 'ascend-vision') == [tmp_path / 'ascend-vision', tmp_path / 'ascend_hub']


def test_a_protected_folder_does_not_hide_the_other_repos(tmp_path, monkeypatch):
    for name in ('$RECYCLE.BIN', 'ascend-vision', 'ascend_hub'):
        (tmp_path / name).mkdir()
    (tmp_path / 'ascend-vision' / '.git').mkdir()
    (tmp_path / 'ascend_hub' / '.git').mkdir()
    real_exists = Path.exists

    def exists(self):
        if self.parent.name == '$RECYCLE.BIN':
            raise PermissionError('denied')
        return real_exists(self)
    monkeypatch.setattr(Path, 'exists', exists)
    assert default_repos(tmp_path / 'ascend-vision') == [tmp_path / 'ascend-vision', tmp_path / 'ascend_hub']


def test_scan_skips_a_repo_that_raises():
    def run_git(repo, *args):
        raise OSError('git missing')
    assert scan([REPO], run_git=run_git, run_gh=lambda *a: None, room=lambda: None, now=NOW) == []


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


class Pacer:
    def __init__(self):
        self.said, self.question_seq = [], 0

    def __call__(self, text, on_said=None, urgent=False):
        self.said.append(text)
        if on_said:
            self.question_seq += 1
            on_said()


def coach_for(tmp_path, leftovers=None, promises=None):
    from assistant.revival import Leftover
    clock, pacer, scans = Clock(), Pacer(), []
    items = leftovers if leftovers is not None else [
        Leftover('branch:a', 'branch', 'branch fix login in ascend hub', NOW - timedelta(days=21),
                 '4 commits not merged'),
        Leftover('room:b', 'room', 'the Agent Room session Add dark mode', NOW - timedelta(days=5), 'You stopped it')]

    def do_scan():
        scans.append(clock())
        return list(items)
    promise = (lambda what, due: promises.append((what, due)) or True) if promises is not None else None
    coach = RevivalCoach(RevivalStore(tmp_path / 'revival.json'), scan=do_scan, announce=pacer, promise=promise,
                         clock=clock, run=lambda job: job())
    return coach, clock, pacer, scans


def test_once_a_week_in_the_day_the_newest_leftover_is_offered(tmp_path):
    coach, clock, pacer, scans = coach_for(tmp_path)
    clock.now = NOW.replace(hour=9)
    coach.tick(True)
    assert pacer.said == []
    clock.now = NOW
    coach.tick(False)
    assert pacer.said == []
    coach.tick(True)
    assert pacer.said == ['Something you left: the Agent Room session Add dark mode, last touched 5 days ago. '
                          'You stopped it. Revive it, later, or forget it?']
    clock.now += timedelta(days=6)
    coach.tick(True)
    assert len(pacer.said) == 1
    clock.now += timedelta(days=1)
    coach.tick(True)
    assert len(pacer.said) == 2


def test_revive_makes_a_promise_for_tomorrow(tmp_path):
    promises = []
    coach, clock, pacer, scans = coach_for(tmp_path, promises=promises)
    coach.tick(True)
    assert coach.intercept('Revive it.') == "Okay. I'll check on it tomorrow."
    assert promises == [('get back to the Agent Room session Add dark mode', datetime(2026, 10, 31, 10, 0))]
    assert coach.intercept('revive it') is None


def test_later_waits_a_month_and_forget_is_for_good(tmp_path):
    coach, clock, pacer, scans = coach_for(tmp_path)
    coach.tick(True)
    assert coach.intercept('later') == "Okay. I'll bring it up again next month."
    clock.now += timedelta(days=7)
    coach.tick(True)
    assert pacer.said[-1].startswith('Something you left: branch fix login in ascend hub, last touched 4 weeks ago.')
    assert coach.intercept('forget it') == "Okay. I won't bring it up again."
    clock.now += timedelta(days=7)
    coach.tick(True)
    assert len(pacer.said) == 2, 'the room session is postponed and the branch forgotten'
    clock.now += timedelta(days=17)
    coach.tick(True)
    assert pacer.said[-1].startswith('Something you left: the Agent Room session Add dark mode')
    saved = json.loads((tmp_path / 'revival.json').read_text())
    assert saved['marks']['branch:a']['status'] == 'forgotten'


def test_answers_only_as_whole_replies_and_only_to_the_latest_question(tmp_path):
    coach, clock, pacer, scans = coach_for(tmp_path)
    coach.tick(True)
    assert coach.intercept('forget it, what time is it') is None
    pacer('Checking in on stretch. Done?', on_said=lambda: None)
    assert coach.intercept('forget it') is None
    coach, clock, pacer, scans = coach_for(tmp_path / 'x')
    coach.tick(True)
    clock.now += timedelta(minutes=3)
    assert coach.intercept('later') is None


def test_what_did_i_leave_lists_up_to_three(tmp_path):
    coach, clock, pacer, scans = coach_for(tmp_path)
    assert coach.intercept('What did I leave behind?') == (
        'Two things: the Agent Room session Add dark mode, 5 days ago; '
        'branch fix login in ascend hub, 3 weeks ago.')
    empty, *_ = coach_for(tmp_path / 'y', leftovers=[])
    assert empty.intercept('anything unfinished') == 'Nothing left behind that I can see.'


def test_a_slow_scan_answers_later(tmp_path):
    from assistant.revival import Leftover
    jobs = []
    coach = RevivalCoach(RevivalStore(tmp_path / 'revival.json'), scan=lambda: [], announce=Pacer(),
                         clock=Clock(), run=jobs.append)
    assert coach.intercept('what did I leave') == 'Let me look. Ask me again in a minute.'
    assert coach.intercept('what did I leave') == 'Let me look. Ask me again in a minute.'
    assert len(jobs) == 1, 'one scan at a time'


def test_nothing_found_still_waits_a_week_before_scanning_again(tmp_path):
    coach, clock, pacer, scans = coach_for(tmp_path, leftovers=[])
    coach.tick(True)
    clock.now += timedelta(hours=2)
    coach.tick(True)
    assert len(scans) == 1 and pacer.said == []  # within a day


def test_a_broken_store_is_set_aside(tmp_path):
    (tmp_path / 'revival.json').write_text('[nope')
    coach, clock, pacer, scans = coach_for(tmp_path)
    coach.tick(True)
    assert (tmp_path / 'revival.json.bad').exists() and len(pacer.said) == 1


def test_wrong_types_in_the_file_do_not_break_it(tmp_path):
    (tmp_path / 'revival.json').write_text(json.dumps(
        {'last_asked': 5, 'marks': {'room:b': 'forgotten', 'branch:a': {'status': 'later', 'until': 7}}}))
    coach, clock, pacer, scans = coach_for(tmp_path)
    coach.tick(True)
    assert len(pacer.said) == 1
    assert coach.intercept('what did I leave').startswith('Two things')


def test_a_bare_no_postpones_instead_of_forgetting(tmp_path):
    coach, clock, pacer, scans = coach_for(tmp_path)
    coach.tick(True)
    assert coach.intercept('No.') == "Okay. I'll bring it up again next month."
    assert json.loads((tmp_path / 'revival.json').read_text())['marks']['room:b']['status'] == 'later'


def test_the_list_rescans_when_the_last_scan_is_old(tmp_path):
    coach, clock, pacer, scans = coach_for(tmp_path)
    coach.intercept('what did I leave')
    clock.now += timedelta(hours=2)
    coach.intercept('what did I leave')
    assert len(scans) == 2


def test_an_empty_scan_tries_again_the_next_day_not_next_week(tmp_path):
    coach, clock, pacer, scans = coach_for(tmp_path, leftovers=[])
    coach.tick(True)
    clock.now += timedelta(hours=2)
    coach.tick(True)
    assert len(scans) == 1
    clock.now += timedelta(days=1)
    coach.tick(True)
    assert len(scans) == 2


# Gap 4: Room queue, stale worktrees, archive

def test_a_left_branch_carries_a_room_start_and_skips_archived_and_worktree_branches():
    run_git = git_for([('main', ago(1)), ('claude/fix-login', ago(21)), ('archive/old', ago(30)),
                       ('in-tree', ago(30))], {'claude/fix-login': 4, 'archive/old': 2, 'in-tree': 3})
    (found,) = left_branches(REPO, run_git, NOW, checked_out={'in-tree'})
    assert (found.repo, found.ref) == (str(REPO), 'claude/fix-login')
    assert found.start == {
        'template': 'feature', 'project': 'claude-fix-login', 'repo': str(REPO),
        'task': ('Finish the work left on branch claude/fix-login (4 commits not merged). Read '
                 '`git log main..claude/fix-login` and `git diff main...claude/fix-login` first, '
                 'then carry that work into this session.')}


def test_a_left_issue_starts_from_the_issue():
    def run_gh(repo, *args):
        return json.dumps([{'number': 12, 'title': 'Crash on start', 'updatedAt': '2026-09-01T10:00:00Z'}])
    (found,) = left_issues(REPO, run_gh, NOW)
    assert found.start == {'template': 'feature', 'project': 'issue-12', 'repo': str(REPO), 'issue': 12}
    assert found.ref == '12'


def test_only_a_stopped_room_session_restarts_from_its_own_start(tmp_path):
    stamp = (NOW - timedelta(days=5)).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    state = tmp_path / 'sessions' / 'a'
    state.mkdir(parents=True)
    (state / 'state.json').write_text(json.dumps({
        'task': 'Add dark mode', 'project': 'dark-mode', 'template': 'feature', 'check': 'npm test',
        'repo': {'dir': 'D:/ascend_hub', 'branch': 'room/dark-mode'}}))
    payload = {'sessions': [{'id': 'a', 'stage': 'stopped', 'title': 'Add dark mode', 'createdAt': stamp},
                            {'id': 'b', 'stage': 'gate-plan', 'title': 'Fix the queue', 'createdAt': stamp},
                            {'id': 'c', 'stage': 'stopped', 'title': 'No state', 'createdAt': stamp}]}
    a, b, c = left_room_sessions(payload, NOW, room_data=tmp_path)
    assert a.start == {'template': 'feature', 'project': 'dark-mode', 'task': 'Add dark mode',
                       'repo': 'D:/ascend_hub', 'check': 'npm test'}
    assert b.start is None and c.start is None


def worktree_git(listing, last, dirty):
    def run_git(repo, *args):
        if args[:2] == ('worktree', 'list'):
            return listing
        if args[0] == 'log':
            return last.get(Path(repo).name)
        if args[0] == 'status':
            return dirty.get(Path(repo).name, '')
        raise AssertionError(args)
    return run_git


def test_stale_worktrees_are_leftovers_and_vision_never_offers_to_start_them():
    from assistant.revival import left_worktrees
    listing = ('worktree D:/ascend_hub\nHEAD aaa\nbranch refs/heads/main\n\n'
               'worktree D:/ascend_hub/.worktrees/old\nHEAD bbb\nbranch refs/heads/feat/old\n\n'
               'worktree D:/ascend_hub/.worktrees/dirty\nHEAD ccc\nbranch refs/heads/feat/dirty\n\n'
               'worktree D:/ascend_hub/.worktrees/fresh\nHEAD ddd\nbranch refs/heads/feat/fresh\n\n'
               'worktree D:/ascend_hub/.worktrees/gone\nHEAD eee\ndetached\n'
               'prunable gitdir file points to non-existent location\n')
    run_git = worktree_git(listing, {'old': f'{ago(20)}\n', 'dirty': f'{ago(40)}\n', 'fresh': f'{ago(2)}\n',
                                     'ascend_hub': f'{ago(60)}\n'},  # a gone folder: its HEAD, read in the repo
                           {'dirty': ' M a.py\n?? b.py\n'})
    found = left_worktrees(REPO, run_git, NOW)
    assert [(f.label, f.detail, f.start) for f in found] == [
        ('worktree old in ascend hub', 'on branch feat/old, nothing uncommitted', None),
        ('worktree dirty in ascend hub', '2 files not committed', None),
        ('worktree gone in ascend hub', 'its folder is gone', None)]
    assert found[0].key == 'worktree:D:/ascend_hub/.worktrees/old'
    assert left_worktrees(REPO, lambda repo, *args: None, NOW) == []


def test_scan_skips_branches_that_a_worktree_already_covers():
    listing = ('worktree D:/r\nHEAD a\nbranch refs/heads/main\n\n'
               'worktree D:/r/.worktrees/w\nHEAD b\nbranch refs/heads/feat/w\n')
    branches = git_for([('main', ago(1)), ('feat/w', ago(30))], {'feat/w': 2})

    def run_git(repo, *args):
        if args[:2] == ('worktree', 'list'):
            return listing
        if args[0] == 'log':
            return f'{ago(30)}\n'
        if args[0] == 'status':
            return ''
        return branches(repo, *args)
    found = scan([Path('D:/r')], run_git=run_git, run_gh=lambda *a: None, room=lambda: None, now=NOW)
    assert [f.kind for f in found] == ['worktree']


def room_coach(tmp_path, queue=None, archive=None, promises=None, items=None):
    from assistant.revival import Leftover
    clock, pacer = Clock(), Pacer()
    start = {'template': 'feature', 'project': 'issue-12', 'repo': 'D:/r', 'issue': 12}
    items = items or [Leftover('issue:D:/r:12', 'issue', 'issue 12 in r, Crash', NOW - timedelta(days=40),
                               'It is still open', repo='D:/r', ref='12', start=start)]
    promise = (lambda what, due: promises.append(what) or True) if promises is not None else None
    coach = RevivalCoach(RevivalStore(tmp_path / 'revival.json'), scan=lambda: list(items), announce=pacer,
                         promise=promise, clock=clock, run=lambda job: job(), queue=queue, archive=archive)
    coach.tick(True)
    return coach, pacer


def marks(folder):
    return json.loads((folder / 'revival.json').read_text())['marks']


def test_revive_queues_the_leftover_in_the_room(tmp_path):
    bodies = []

    def queue(body):
        bodies.append(body)
        return True, 'Queued in the Agent Room.'
    coach, pacer = room_coach(tmp_path, queue=queue, archive=lambda item: (True, 'ok'))
    assert pacer.said[-1].endswith('Revive it, archive it, later, or forget it?')
    assert coach.intercept('revive it') == 'Queued in the Agent Room.'
    assert bodies == [{'template': 'feature', 'project': 'issue-12', 'repo': 'D:/r', 'issue': 12}]
    assert marks(tmp_path)['issue:D:/r:12']['status'] == 'revived'


def test_a_failed_room_queue_marks_nothing_and_no_start_means_a_promise(tmp_path):
    from assistant.revival import Leftover
    coach, pacer = room_coach(tmp_path, queue=lambda body: (False, 'I could not reach the agent room.'))
    assert coach.intercept('revive it') == 'I could not reach the agent room.'
    assert marks(tmp_path) == {}
    promises = []
    tree = Leftover('worktree:D:/r/w', 'worktree', 'worktree w in r', NOW - timedelta(days=30),
                    'its folder is gone', repo='D:/r', ref='D:/r/w')
    coach, pacer = room_coach(tmp_path / 'x', queue=lambda body: (True, 'q'), promises=promises, items=[tree])
    assert coach.intercept('revive it') == "Okay. I'll check on it tomorrow."
    assert promises == ['get back to worktree w in r']


def test_the_room_queue_runs_outside_the_coach_lock(tmp_path):
    import threading
    got = []

    def queue(body):
        def try_lock():
            took = coach._lock.acquire(timeout=0.5)
            got.append(took)
            if took:
                coach._lock.release()
        other = threading.Thread(target=try_lock)
        other.start()
        other.join()
        return True, 'Queued.'

    coach, pacer = room_coach(tmp_path, queue=queue)
    assert coach.intercept('revive it') == 'Queued.' and got == [True]


def test_archive_puts_it_away_and_drop_forgets(tmp_path):
    archived = []

    def archive(item):
        archived.append(item.ref)
        return True, 'Closed issue 12.'
    coach, pacer = room_coach(tmp_path, archive=archive)
    assert coach.intercept('archive it') == 'Closed issue 12.'
    assert archived == ['12']
    assert marks(tmp_path)['issue:D:/r:12']['status'] == 'archived'
    coach, pacer = room_coach(tmp_path / 'b', archive=lambda item: (False, 'I could not close issue 12.'))
    assert coach.intercept('archive') == 'I could not close issue 12.'
    assert marks(tmp_path / 'b') == {}
    coach, pacer = room_coach(tmp_path / 'c')
    assert pacer.said[-1].endswith('Revive it, later, or forget it?')
    assert coach.intercept('drop it') == "Okay. I won't bring it up again."


def test_the_archiver_renames_branches_closes_issues_and_never_deletes():
    from assistant.revival import Leftover, make_archiver
    calls = []

    def run(repo, *args):
        calls.append((str(repo), args))
        return None if 'fail' in args[2] else ''
    archive = make_archiver(run, run)
    branch = Leftover('b', 'branch', 'branch x', NOW, 'd', repo='D:/r', ref='feat/x')
    issue = Leftover('i', 'issue', 'issue 12', NOW, 'd', repo='D:/r', ref='12')
    tree = Leftover('w', 'worktree', 'worktree w', NOW, 'd', repo='D:/r', ref='D:/r/w')
    room = Leftover('s', 'room', 'session', NOW, 'd')
    assert archive(branch) == (True, 'Renamed the branch to archive/feat/x. Its commits are all still there.')
    assert archive(issue) == (True, 'Closed issue 12 as not planned. You can reopen it on GitHub.')
    assert archive(tree)[1] == "Okay. I'll stop bringing it up; I never delete worktrees or sessions."
    assert archive(room)[0] is True
    assert calls == [('D:/r', ('branch', '-m', 'feat/x', 'archive/feat/x')),
                     ('D:/r', ('issue', 'close', '12', '--reason', 'not planned'))]
    failing = Leftover('b', 'branch', 'branch x', NOW, 'd', repo='D:/r', ref='fail')
    assert archive(failing) == (False, "I couldn't rename that branch; it may be checked out somewhere.")


# Gap 3 review fixes

def test_named_repo_needs_two_words_or_the_word_repo_and_prefers_the_longest():
    from assistant.revival import named_repo
    dirs = ['D:/app', 'D:/app-server', 'D:/ascend_hub', 'D:/notes']
    assert named_repo('install the app', dirs) is None
    assert named_repo('take notes on the meeting', dirs) is None
    assert named_repo('set up the app repo', dirs) == 'D:/app'
    assert named_repo('in the notes project', dirs) == 'D:/notes'
    assert named_repo('app server setup', dirs) == 'D:/app-server'
    assert named_repo('deploy ascend hub', dirs) == 'D:/ascend_hub'
    assert named_repo('fix the ascend_hub login', dirs) == 'D:/ascend_hub'
