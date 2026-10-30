from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from assistant.revival import (RevivalCoach, RevivalStore, default_repos, left_branches, left_issues,
                               left_room_sessions, scan)

NOW = datetime(2026, 10, 30, 11, 0)
REPO = Path('D:/ascend_hub')


def ago(days):
    return int((NOW - timedelta(days=days)).timestamp())


def git_for(branches, ahead, default='main'):
    def run_git(repo, *args):
        if args[:3] == ('rev-parse', '--verify', '--quiet'):
            return 'abc123\n' if args[3] == f'refs/heads/{default}' else None
        if args[0] == 'for-each-ref':
            return ''.join(f'{name}\t{stamp}\n' for name, stamp in branches)
        if args[0] == 'rev-list':
            return f"{ahead[args[2].split('..')[1]]}\n"
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
    assert len(scans) == 1 and pacer.said == []


def test_a_broken_store_is_set_aside(tmp_path):
    (tmp_path / 'revival.json').write_text('[nope')
    coach, clock, pacer, scans = coach_for(tmp_path)
    coach.tick(True)
    assert (tmp_path / 'revival.json.bad').exists() and len(pacer.said) == 1
