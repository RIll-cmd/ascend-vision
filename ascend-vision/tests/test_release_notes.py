from datetime import datetime
import json
import subprocess

from assistant.release_notes import (ReleaseNotes, fallback_story, group, parse_log, parse_story, render_notes)
from assistant.revival import command_runner

NOW = datetime(2026, 10, 30, 15, 0)
SEP, END = '\x1f', '\x1e'


def make_repo(path, subjects):
    path.mkdir(parents=True)
    git = lambda *args: subprocess.run(['git', *args], cwd=path, check=True, capture_output=True)
    git('init', '-q', '-b', 'main')
    git('config', 'user.email', 'owner@example.test')
    git('config', 'user.name', 'Owner')
    for n, subject in enumerate(subjects):
        (path / f'f{n}.txt').write_text(subject, encoding='utf-8')
        git('add', '.')
        git('commit', '-q', '-m', subject)
    return git


def notes_for(tmp_path, repos, story=None):
    said, jobs = [], []
    notes = ReleaseNotes(repos=lambda: repos, run_git=command_runner('git'), write_story=story,
                         folder=tmp_path / 'notes', announce=said.append, clock=lambda: NOW, run=jobs.append)
    return notes, said, jobs


def finish(jobs):
    while jobs:
        jobs.pop(0)()


def test_parse_and_group_conventional_subjects():
    log = (f'a1{SEP}feat(room): weekly scorecard push{SEP}2026-10-29T10:00:00+08:00{END}\n'
           f'b2{SEP}fix: score stopped work{SEP}2026-10-29T11:00:00+08:00{END}\n'
           f'c3{SEP}docs: slump detector done{SEP}2026-10-29T12:00:00+08:00{END}\n'
           f'd4{SEP}Update README.md{SEP}2026-10-29T13:00:00+08:00{END}\n'
           f'e5{SEP}refactor!: split the server{SEP}2026-10-29T14:00:00+08:00{END}')
    commits = parse_log(log)
    assert [(c.sha, c.kind, c.subject) for c in commits] == [
        ('a1', 'feat', 'weekly scorecard push'), ('b2', 'fix', 'score stopped work'),
        ('c3', 'docs', 'slump detector done'), ('d4', 'other', 'Update README.md'),
        ('e5', 'other', 'split the server')]
    assert [len(group(commits)[k]) for k in ('New', 'Fixes', 'Docs', 'Other')] == [1, 1, 1, 2]


def test_fallback_story_and_story_checks():
    commits = parse_log(''.join(f'{n}{SEP}feat: thing {n}{SEP}2026-10-29T10:00:00Z{END}' for n in range(7)))
    story = fallback_story('ascend hub', commits)
    assert story['whats_new'] == ['Thing 0', 'Thing 1', 'Thing 2', 'Thing 3', 'Thing 4']
    assert story['post'] == 'New in ascend hub: thing 0, thing 1 and thing 2.'
    assert parse_story({'whats_new': ['A'], 'post': 'p'}) == {'whats_new': ['A'], 'post': 'p'}
    for bad in (None, [], {'whats_new': 'A', 'post': 'p'}, {'whats_new': [], 'post': 'p'},
                {'whats_new': ['A'], 'post': 'x' * 281}):
        assert parse_story(bad) is None


def test_notes_for_a_named_repo_then_only_whats_new_since(tmp_path):
    git = make_repo(tmp_path / 'ascend_hub', ['feat(room): weekly scorecard push', 'fix(room): score stopped work',
                                              'docs: slump detector done'])
    make_repo(tmp_path / 'other', ['feat: unrelated'])
    notes, said, jobs = notes_for(tmp_path, [tmp_path / 'ascend_hub', tmp_path / 'other'])
    assert notes.intercept('Vision, write release notes for Ascend Hub.') == (
        'Writing the release notes for ascend hub.')
    assert notes.intercept('write release notes') == 'Still writing the last ones.'
    finish(jobs)
    assert said == ['Release notes for ascend hub are ready: 3 changes, 1 new and 1 fix.']
    text = (tmp_path / 'notes' / 'ascend_hub-2026-10-30.md').read_text(encoding='utf-8')
    assert text.startswith('# ascend hub: release notes, 2026-10-30\n')
    assert '## What\'s new\n\n- Weekly scorecard push\n' in text and '### Fixes\n\n- score stopped work (' in text
    assert 'New in ascend hub: weekly scorecard push.' in text
    assert notes.intercept('write release notes for ascend hub') == 'Writing the release notes for ascend hub.'
    finish(jobs)
    assert said[-1] == 'Nothing new in ascend hub since the last notes.'
    (tmp_path / 'ascend_hub' / 'g.txt').write_text('x')
    git('add', '.')
    git('commit', '-q', '-m', 'feat: notes button')
    notes.intercept('write release notes for ascend hub')
    finish(jobs)
    second = (tmp_path / 'notes' / 'ascend_hub-2026-10-30-2.md').read_text(encoding='utf-8')
    assert 'notes button' in second and 'scorecard' not in second


def test_a_rebased_away_last_commit_falls_back_to_recent_days(tmp_path):
    make_repo(tmp_path / 'app', ['feat: one'])
    notes, said, jobs = notes_for(tmp_path, [tmp_path / 'app'])
    (tmp_path / 'notes').mkdir()
    (tmp_path / 'notes' / 'state.json').write_text(json.dumps({'app': 'deadbeef' * 5}))
    notes.intercept('write release notes for app')
    finish(jobs)
    assert said == ['Release notes for app are ready: 1 change, 1 new and no fixes.']


def test_unknown_repo_and_the_newest_repo_by_default(tmp_path):
    make_repo(tmp_path / 'old', ['feat: old thing'])
    make_repo(tmp_path / 'new', ['feat: new thing'])
    notes, said, jobs = notes_for(tmp_path, [tmp_path / 'old', tmp_path / 'new'])
    assert notes.intercept('write release notes for the moon') is None
    assert notes.intercept('write the release notes').startswith('Writing the release notes for ')


def test_the_ai_story_is_used_and_the_owner_is_never_quoted_wrong(tmp_path):
    make_repo(tmp_path / 'app', ['feat: dark mode', 'fix: crash on start'])
    asked = []

    def story(name, commits):
        asked.append((name, [c.subject for c in commits]))
        return {'whats_new': ['You can switch to dark mode.'], 'post': 'Dark mode is here, and the start crash is gone.'}
    notes, said, jobs = notes_for(tmp_path, [tmp_path / 'app'], story=story)
    notes.intercept('write release notes for app')
    finish(jobs)
    assert asked == [('app', ['crash on start', 'dark mode'])]
    text = (tmp_path / 'notes' / 'app-2026-10-30.md').read_text(encoding='utf-8')
    assert '- You can switch to dark mode.' in text and 'Dark mode is here' in text


def test_a_quiet_repo_with_no_notes_yet_gets_its_last_commits(tmp_path):
    git = make_repo(tmp_path / 'quiet', [])
    (tmp_path / 'quiet' / 'a.txt').write_text('a')
    git('add', '.')
    subprocess.run(['git', 'commit', '-q', '-m', 'feat: old work'], cwd=tmp_path / 'quiet', check=True,
                   env={**__import__('os').environ, 'GIT_COMMITTER_DATE': '2026-08-01T10:00:00',
                        'GIT_AUTHOR_DATE': '2026-08-01T10:00:00'})
    notes, said, jobs = notes_for(tmp_path, [tmp_path / 'quiet'])
    notes.intercept('write release notes for quiet')
    finish(jobs)
    assert said == ['Release notes for quiet are ready: 1 change, 1 new and no fixes.']


def test_unknown_names_go_to_chat_and_partial_names_match(tmp_path):
    make_repo(tmp_path / 'ascend-vision', ['feat: wake word'])
    notes, said, jobs = notes_for(tmp_path, [tmp_path / 'ascend-vision'])
    assert notes.intercept('write release notes for the new login page') is None
    assert notes.intercept('write some release notes for vision please') == (
        'Writing the release notes for ascend vision.')


def test_odd_bytes_in_a_subject_keep_the_commit_and_long_ai_lists_are_trimmed():
    commits = parse_log(f'a1{SEP}feat: odd {SEP} subject{SEP}2026-10-29T10:00:00Z{END}')
    assert [c.subject for c in commits] == ['odd   subject']
    assert parse_story({'whats_new': ['A'] * 7, 'post': 'p'}) == {'whats_new': ['A'] * 5, 'post': 'p'}


def test_markdown_in_subjects_stays_plain():
    commits = parse_log(f'a1{SEP}feat: # big ![x](http://e) <b>{SEP}2026-10-29T10:00:00Z{END}')
    text = render_notes('app', NOW, commits, fallback_story('app', commits))
    assert '\n# big' not in text and '![x]' not in text and '<b>' not in text


# Gap 7: your voice, screenshots

def test_voice_samples_come_from_the_owners_own_posts(tmp_path):
    from assistant.release_notes import read_voice
    folder = tmp_path / 'notes'
    assert read_voice(folder) == []
    voice = (folder / 'voice.md').read_text(encoding='utf-8')
    assert voice.startswith('<!--') and read_voice(folder) == []  # the how-to comment is not a sample
    (folder / 'voice.md').write_text(voice + '\nShipped it! Dark mode is live 🌙 #buildinpublic\n\n'
                                     'tiny fix today, the login works again\n\n' + 'x' * 3000, encoding='utf-8')
    assert read_voice(folder) == ['Shipped it! Dark mode is live 🌙 #buildinpublic',
                                  'tiny fix today, the login works again']


def test_the_story_writer_hands_the_samples_to_the_ai():
    from assistant.release_notes import make_story_writer
    asked = []

    class Router:
        def generate_structured_response(self, prompt, **kwargs):
            asked.append((prompt, kwargs['system_prompt']))
            return {'whats_new': ['A'], 'post': 'p'}
    commits = parse_log(f'a1{SEP}feat: dark mode{SEP}2026-10-29T10:00:00Z{END}')
    make_story_writer(Router(), voice=lambda: ['Shipped it! 🌙'])('ascend hub', commits)
    prompt, system = asked[0]
    assert 'Shipped it! 🌙' in prompt and 'never copy' in system
    make_story_writer(Router(), voice=lambda: [])('ascend hub', commits)
    assert 'owner wrote' not in asked[1][0]


def test_screenshots_are_taken_for_the_repo_and_linked(tmp_path):
    from assistant.release_notes import make_screenshotter
    hub = tmp_path / 'ascend_hub'
    make_repo(hub, ['feat: dark mode'])
    taken = []

    def shoot(url, path):
        taken.append((url, path.name))
        path.write_bytes(b'png')
    shots = make_screenshotter(['ascend_hub=http://127.0.0.1:5173/', 'ascend_hub=http://127.0.0.1:5173/room',
                                'other=http://x/', 'broken line'], shoot)
    notes, said, jobs = notes_for(tmp_path, [hub])
    notes.screenshots = shots
    notes.intercept('write release notes for ascend hub')
    finish(jobs)
    assert taken == [('http://127.0.0.1:5173/', 'ascend_hub-2026-10-30-shot-1.png'),
                     ('http://127.0.0.1:5173/room', 'ascend_hub-2026-10-30-shot-2.png')]
    text = (tmp_path / 'notes' / 'ascend_hub-2026-10-30.md').read_text(encoding='utf-8')
    assert '## Screenshots' in text and '![Screenshot 1](ascend_hub-2026-10-30-shot-1.png)' in text
    assert 'voice.md' not in said[-1]  # the hint is for an AI-written post only
    notes, said, jobs = notes_for(tmp_path / 'ai', [hub], story=lambda name, commits: {'whats_new': ['A'], 'post': 'p'})
    notes.intercept('write release notes for ascend hub')
    finish(jobs)
    assert said[-1].endswith('Add a few of your own posts to voice.md so the post sounds like you.')


def test_a_failed_screenshot_is_skipped_and_said(tmp_path):
    from assistant.release_notes import make_screenshotter
    hub = tmp_path / 'ascend_hub'
    make_repo(hub, ['feat: dark mode'])

    def shoot(url, path):
        raise TimeoutError('server down')
    notes, said, jobs = notes_for(tmp_path, [hub])
    notes.screenshots = make_screenshotter(['ascend_hub=http://127.0.0.1:5173/'], shoot)
    (tmp_path / 'notes').mkdir()
    (tmp_path / 'notes' / 'voice.md').write_text('my own post\n', encoding='utf-8')
    notes.intercept('write release notes for ascend hub')
    finish(jobs)
    assert '## Screenshots' not in (tmp_path / 'notes' / 'ascend_hub-2026-10-30.md').read_text(encoding='utf-8')
    assert said[-1].endswith('The screenshot did not work, so the notes have none.')


def test_a_second_run_the_same_day_keeps_the_first_runs_screenshot(tmp_path):
    from assistant.release_notes import make_screenshotter
    hub = tmp_path / 'ascend_hub'
    git = make_repo(hub, ['feat: dark mode'])

    def shoot(url, path):
        path.write_bytes(path.name.encode())
    notes, said, jobs = notes_for(tmp_path, [hub])
    notes.screenshots = make_screenshotter(['ascend_hub=http://127.0.0.1:5173/'], shoot)
    notes.intercept('write release notes for ascend hub')
    finish(jobs)
    (hub / 'more.txt').write_text('x')
    git('add', '.')
    git('commit', '-q', '-m', 'fix: login')
    notes.intercept('write release notes for ascend hub')
    finish(jobs)
    folder = tmp_path / 'notes'
    assert (folder / 'ascend_hub-2026-10-30-shot-1.png').read_bytes() == b'ascend_hub-2026-10-30-shot-1.png'
    assert '![Screenshot 1](ascend_hub-2026-10-30-2-shot-1.png)' in (folder / 'ascend_hub-2026-10-30-2.md').read_text(
        encoding='utf-8')
