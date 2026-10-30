from datetime import datetime, timedelta
import json

from PIL import Image

from assistant.show_me import SILENT_ACK, HowToRecorder, fallback_steps, parse_steps, render_guide

START = datetime(2026, 10, 30, 14, 0)


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now


class Screen:
    def __init__(self):
        self.shade, self.fail = 0, False

    def __call__(self):
        if self.fail:
            raise OSError('screen locked')
        return Image.new('RGB', (2400, 1200), (self.shade, self.shade, self.shade))


def recorder_for(tmp_path, write_steps=None, **kwargs):
    clock, screen, said = Clock(), Screen(), []
    recorder = HowToRecorder(tmp_path / 'howto', announce=said.append, capture=screen, write_steps=write_steps,
                             clock=clock, run=lambda job: job(), **kwargs)
    return recorder, clock, screen, said


def guide_dir(tmp_path):
    (folder,) = (tmp_path / 'howto').iterdir()
    return folder


def test_a_walkthrough_becomes_a_guide_with_one_shot_per_line(tmp_path):
    recorder, clock, screen, said = recorder_for(tmp_path)
    assert recorder.intercept('what time is it') is None
    assert recorder.intercept('Vision, watch me set up the printer') == (
        "Recording. Talk me through each step, and say 'done recording' when you finish.")
    assert recorder.recording
    clock.now += timedelta(seconds=20)
    assert recorder.intercept('Open Settings and go to Printers.') == SILENT_ACK
    clock.now += timedelta(seconds=40)
    assert recorder.intercept('Click Add device, then pick the HP.') == SILENT_ACK
    assert recorder.intercept('done recording') == 'Got it. Writing the guide now.'
    assert not recorder.recording
    assert said == ['Your how-to is ready: set up the printer, 2 steps.']
    folder = guide_dir(tmp_path)
    assert folder.name == '2026-10-30-set-up-the-printer'
    guide = (folder / 'guide.md').read_text(encoding='utf-8')
    assert guide.startswith('# Set up the printer\n')
    assert '1. Open Settings and go to Printers.' in guide and '![Step 1](shot-02.jpg)' in guide
    assert '2. Click Add device, then pick the HP.' in guide and '![Step 2](shot-03.jpg)' in guide
    with Image.open(folder / 'shot-02.jpg') as shot:
        assert shot.width == 1600


def test_the_ai_writes_the_steps_from_the_transcript_text_only(tmp_path):
    asked = []

    def write_steps(lines):
        asked.append(lines)
        return {'title': 'Add a printer', 'summary': 'Add the office HP printer.',
                'steps': [{'text': 'Open Settings, then Printers.', 'from': [1]},
                          {'text': 'Choose Add device and pick the HP.', 'from': [2, 3]}],
                'checklist': ['The test page prints.']}
    recorder, clock, screen, said = recorder_for(tmp_path, write_steps=write_steps)
    recorder.intercept('watch me')
    for line in ('open settings', 'uh go to printers and add device', 'pick the HP one'):
        clock.now += timedelta(seconds=10)
        recorder.intercept(line)
    recorder.intercept('stop recording')
    assert asked == [['open settings', 'uh go to printers and add device', 'pick the HP one']]
    guide = (guide_dir(tmp_path) / 'guide.md').read_text(encoding='utf-8')
    assert guide.startswith('# Add a printer\n\nAdd the office HP printer.\n')
    assert '2. Choose Add device and pick the HP.' in guide and '![Step 2](shot-03.jpg)' in guide
    assert '- [ ] The test page prints.' in guide
    assert said == ['Your how-to is ready: Add a printer, 2 steps.']


def test_the_owners_title_wins_and_a_bad_ai_answer_falls_back(tmp_path):
    recorder, clock, screen, said = recorder_for(tmp_path, write_steps=lambda lines: {'steps': [{'text': 'x',
                                                                                               'from': [9]}]})
    recorder.intercept('record a how-to renew the certificate')
    recorder.intercept('run certbot renew')
    recorder.intercept('done recording')
    guide = (guide_dir(tmp_path) / 'guide.md').read_text(encoding='utf-8')
    assert guide.startswith('# Renew the certificate\n') and '1. Run certbot renew' in guide


def test_parse_steps_rejects_everything_out_of_shape():
    good = {'title': 'T', 'summary': '', 'steps': [{'text': 'a', 'from': [1]}], 'checklist': []}
    assert parse_steps(good, 1)['steps'] == [{'text': 'a', 'from': [1]}]
    for bad in (None, [], {'steps': []}, {'steps': [{'text': '', 'from': [1]}]}, {'steps': [{'text': 'a', 'from': [2]}]},
                {'steps': [{'text': 'a', 'from': []}]}, {'steps': [{'text': 'a', 'from': [1]}] * 31},
                {'steps': [{'text': 'a', 'from': ['1']}]}, {'steps': [{'text': 'a' * 401, 'from': [1]}]}):
        assert parse_steps(bad, 1) is None
    assert fallback_steps(['a', 'b'])['steps'] == [{'text': 'A', 'from': [1]}, {'text': 'B', 'from': [2]}]


def test_the_screen_is_also_shot_when_it_changes(tmp_path):
    recorder, clock, screen, said = recorder_for(tmp_path)
    recorder.intercept('watch me')
    recorder.intercept('open the app')
    clock.now += timedelta(seconds=15)
    recorder.tick()  # nothing changed
    screen.shade = 200
    clock.now += timedelta(seconds=10)
    recorder.tick()  # too soon after the last check
    clock.now += timedelta(seconds=5)
    recorder.tick()
    recorder.intercept('done recording')
    guide = (guide_dir(tmp_path) / 'guide.md').read_text(encoding='utf-8')
    assert '![Step 1](shot-02.jpg)' in guide and '![Step 1, later](shot-03.jpg)' in guide


def test_cancel_keeps_nothing_and_a_failing_screen_keeps_the_words(tmp_path):
    recorder, clock, screen, said = recorder_for(tmp_path)
    recorder.intercept('watch me')
    assert recorder.intercept('cancel recording') == 'Recording thrown away.'
    assert not (tmp_path / 'howto').exists() or not any((tmp_path / 'howto').iterdir())
    screen.fail = True
    recorder.intercept('show me once')
    recorder.intercept('type the password into the vault')
    recorder.intercept('done recording')
    guide = (guide_dir(tmp_path) / 'guide.md').read_text(encoding='utf-8')
    assert '1. Type the password into the vault' in guide and '![' not in guide


def test_it_stops_by_itself_after_thirty_minutes(tmp_path):
    recorder, clock, screen, said = recorder_for(tmp_path)
    recorder.intercept('watch me')
    recorder.intercept('step one')
    clock.now += timedelta(minutes=30)
    recorder.tick()
    assert not recorder.recording
    assert said == ['I stopped recording after thirty minutes.', 'Your how-to is ready: How-to, 1 step.']


def test_shots_stop_at_the_limit_but_words_keep_coming(tmp_path):
    recorder, clock, screen, said = recorder_for(tmp_path, max_shots=2)
    recorder.intercept('watch me')
    for line in ('one', 'two', 'three'):
        recorder.intercept(line)
    recorder.intercept('done recording')
    folder = guide_dir(tmp_path)
    assert sorted(p.name for p in folder.glob('*.jpg')) == ['shot-01.jpg', 'shot-02.jpg']
    assert '3. Three' in (folder / 'guide.md').read_text(encoding='utf-8')


def test_same_title_same_day_gets_its_own_folder(tmp_path):
    recorder, clock, screen, said = recorder_for(tmp_path)
    for _ in range(2):
        recorder.intercept('watch me back up the phone')
        recorder.intercept('plug it in')
        recorder.intercept('done recording')
    assert sorted(p.name for p in (tmp_path / 'howto').iterdir()) == [
        '2026-10-30-back-up-the-phone', '2026-10-30-back-up-the-phone-2']


def test_narration_cannot_break_the_guide_structure():
    guide = render_guide('T', '', [{'text': '# not a heading\n![x](http://evil)', 'from': [1]}], [], {})
    assert '\n# not a heading' not in guide and '![x]' not in guide
    assert json.dumps(guide)  # plain text
