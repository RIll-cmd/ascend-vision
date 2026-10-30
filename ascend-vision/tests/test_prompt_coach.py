from datetime import datetime, timedelta, timezone
import json

from assistant.prompt_coach import PromptCoach, coaching, grade, read_sessions, traits

NOW = datetime(2026, 10, 30, 11, 0)
REVISION = 'Owner asked for changes to the plan: shorter.'


def write_session(root, n, *, task, stage='done', days=2, tasks=None, revisions=0, check=None, extra=None):
    created = (NOW - timedelta(days=days)).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    folder = root / 'sessions' / f'{1793000000000 + n}-{n:06x}'
    folder.mkdir(parents=True)
    state = {'id': folder.name, 'task': task, 'stage': stage, 'createdAt': created, 'check': check,
             'plan': {'tasks': tasks if tasks is not None else [{'status': 'done', 'attempts': 0, 'rounds': 1}]},
             **(extra or {})}
    (folder / 'state.json').write_text(json.dumps(state), encoding='utf-8')
    lines = [{'from': 'room', 'kind': 'note', 'text': REVISION}] * revisions + [{'from': 'codex', 'text': 'hi'}]
    (folder / 'transcript.jsonl').write_text('\n'.join(map(json.dumps, lines)) + '\nnot json\n', encoding='utf-8')
    return folder


def test_traits_are_read_from_the_prompt_text():
    found = traits('Fix @app/page.tsx so the header is sticky. It should stay on top.\n- keep the logo\n'
                   'For example like github.com', check='npm test')
    assert found == {'files': True, 'done_when': True, 'check': True, 'steps': True, 'example': True}
    assert traits('make it better', check=None) == dict.fromkeys(found, False)


def test_grade_counts_rework_and_its_reasons(tmp_path):
    write_session(tmp_path, 1, task='Smooth one', tasks=[{'status': 'done', 'attempts': 0, 'rounds': 1}])
    write_session(tmp_path, 2, task='Rough one', stage='rejected', revisions=2,
                  tasks=[{'status': 'done', 'attempts': 1, 'rounds': 3, 'check': {'ok': False}}])
    smooth, rough = sorted((grade(s) for s in read_sessions(tmp_path, NOW)), key=lambda g: g.rework)
    assert smooth.smooth and smooth.rework == 0 and smooth.title == 'Smooth one'
    assert not rough.smooth and rough.rework == 2 + 2 + 1 + 1 + 3
    assert rough.reasons['plan revisions'] == 2 and rough.reasons['review rounds'] == 2


def test_reading_skips_old_broken_and_never_started_sessions(tmp_path):
    write_session(tmp_path, 1, task='Recent')
    write_session(tmp_path, 2, task='Old', days=40)
    write_session(tmp_path, 3, task='Stopped before work', stage='stopped',
                  tasks=[{'status': 'pending', 'attempts': 0, 'rounds': 0}])
    write_session(tmp_path, 4, task='Future', days=-3)
    (write_session(tmp_path, 5, task='Broken') / 'state.json').write_text('{nope', encoding='utf-8')
    write_session(tmp_path, 6, task=None, extra={'question': 'Which database?', 'mode': 'debate'}, tasks=[])
    assert sorted(s['state'].get('task') or s['state'].get('question') for s in read_sessions(tmp_path, NOW)) == [
        'Recent', 'Which database?']
    assert read_sessions(tmp_path / 'missing', NOW) == []


def month(tmp_path, smooth, rough):
    for n in range(smooth):
        write_session(tmp_path, n, task=f'Edit @src/file{n}.py so tests pass')
    for n in range(rough):
        write_session(tmp_path, 100 + n, task=f'Add dark mode {n}', revisions=1 + n)
    return [grade(s) for s in read_sessions(tmp_path, NOW)]


def test_coaching_names_the_trait_the_smooth_prompts_had(tmp_path):
    assert coaching(month(tmp_path, 7, 5)) == [
        'Your Agent Room prompts, last 30 days: 12 sessions; 7 went smoothly and 5 needed rework, '
        'mostly plan revisions.',
        'The smooth ones more often named the files: 7 of 7, against 0 of 5.',
        'Next time, name the files to change.']


def test_coaching_falls_back_to_the_roughest_prompt(tmp_path):
    for n in range(4):
        write_session(tmp_path, n, task=f'Thing {n}')
    for n in range(3):
        write_session(tmp_path, 100 + n, task=f'Add dark mode {n}', revisions=1 + n)
    lines = coaching([grade(s) for s in read_sessions(tmp_path, NOW)])
    assert lines[1] == "The roughest was 'Add dark mode 2', with 3 rounds of rework."
    assert lines[2] == 'Next time, name the files to change.'


def test_too_few_sessions_or_all_smooth(tmp_path):
    assert coaching(month(tmp_path / 'a', 3, 2)) == []
    assert coaching(month(tmp_path / 'b', 6, 0)) == ['All 6 went smoothly. Keep writing them like that.']


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


def coach_for(tmp_path, smooth=7, rough=5):
    month(tmp_path / 'room', smooth, rough)
    clock, said = Clock(), []
    coach = PromptCoach(lambda: [grade(s) for s in read_sessions(tmp_path / 'room', clock())],
                        announce=said.append, store_path=tmp_path / 'prompt_coach.json', clock=clock)
    return coach, clock, said


def test_weekly_in_the_day_and_not_again_after_a_restart(tmp_path):
    coach, clock, said = coach_for(tmp_path)
    clock.now = NOW.replace(hour=8)
    coach.tick(True)
    assert said == []
    clock.now = NOW
    coach.tick(False)
    coach.tick(True)
    assert said == [' '.join(coaching(coach.read()))]
    again = PromptCoach(coach.read, announce=said.append, store_path=tmp_path / 'prompt_coach.json', clock=clock)
    clock.now += timedelta(days=6)
    again.tick(True)
    assert len(said) == 1
    clock.now += timedelta(days=1)
    again.tick(True)
    assert len(said) == 2


def test_asking_anytime(tmp_path):
    coach, clock, said = coach_for(tmp_path)
    assert coach.intercept('Vision, how are my prompts?').startswith('Your Agent Room prompts, last 30 days')
    assert coach.intercept('what are my prompts about') is None
    few, *_ = coach_for(tmp_path / 'few', smooth=2, rough=1)
    assert few.intercept('prompt tips') == 'Not enough Agent Room sessions in the last 30 days to compare yet.'


def test_nothing_to_say_is_silent_on_the_weekly_turn(tmp_path):
    coach, clock, said = coach_for(tmp_path, smooth=2, rough=1)
    coach.tick(True)
    assert said == []
