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


import pytest


@pytest.mark.parametrize('chat', ['watch me play this song', 'Hey, watch me now', 'Show me once more'])
def test_ordinary_chat_does_not_start_a_recording(tmp_path, chat):
    recorder, *_ = recorder_for(tmp_path)
    assert recorder.intercept(chat) is None and not recorder.recording


@pytest.mark.parametrize('stop', ["I'm done recording", 'Stop the recording', 'Done recording, thanks'])
def test_natural_ways_to_stop(tmp_path, stop):
    recorder, *_ = recorder_for(tmp_path)
    recorder.intercept('watch me')
    recorder.intercept('open the app')
    assert recorder.intercept(stop) == 'Got it. Writing the guide now.' and not recorder.recording


def test_the_title_keeps_the_owners_own_words(tmp_path):
    recorder, clock, screen, said = recorder_for(tmp_path)
    recorder.intercept('Watch me set up printer 2 for the SSL cert.')
    recorder.intercept('open the panel')
    recorder.intercept('done recording')
    assert (guide_dir(tmp_path) / 'guide.md').read_text(encoding='utf-8').startswith(
        '# Set up printer 2 for the SSL cert\n')


def test_a_transcript_too_long_for_the_ai_is_written_line_by_line(tmp_path):
    asked = []
    recorder, *_ = recorder_for(tmp_path, write_steps=lambda lines: asked.append(lines) or None, max_shots=1)
    recorder.intercept('watch me')
    for n in range(400):
        recorder.intercept(f'step number {n} of the long procedure we follow')
    recorder.intercept('done recording')
    assert asked == [] and '400. Step number 399' in (guide_dir(tmp_path) / 'guide.md').read_text(encoding='utf-8')


def test_a_guide_is_on_disk_after_every_line_in_case_vision_quits(tmp_path):
    recorder, *_ = recorder_for(tmp_path)
    recorder.intercept('watch me back up the phone')
    recorder.intercept('plug it in')
    guide = (guide_dir(tmp_path) / 'guide.md').read_text(encoding='utf-8')
    assert '1. Plug it in' in guide


def test_a_folder_that_cannot_be_made_does_not_leave_it_recording(tmp_path):
    (tmp_path / 'howto').write_text('a file, not a folder')
    recorder, *_ = recorder_for(tmp_path)
    assert recorder.intercept('watch me') == "I couldn't start recording: the how-to folder is not writable."
    assert not recorder.recording and recorder.intercept('hello') is None


def test_stopping_with_nothing_said_keeps_nothing(tmp_path):
    recorder, clock, screen, said = recorder_for(tmp_path)
    recorder.intercept('watch me')
    assert recorder.intercept('done recording') == 'Nothing was said, so I kept nothing.'
    assert said == [] and not any((tmp_path / 'howto').iterdir())


def test_narration_cannot_break_the_guide_structure():
    guide = render_guide('T', '', [{'text': '# not a heading\n![x](http://evil)', 'from': [1]}], [], {})
    assert '\n# not a heading' not in guide and '![x]' not in guide
    html = render_guide('T', '', [{'text': '<img src="http://x/a.png"> [a]: http://evil', 'from': [1]}], [], {})
    assert '<img' not in html and '\n[a]:' not in html
    assert json.dumps(guide)  # plain text


# Gap 3: screen video, repo docs, Room memory

class FakeVideo:
    made = []

    def __init__(self, path):
        self.path, self.stopped = path, False
        path.write_bytes(b'mp4')
        FakeVideo.made.append(self)

    def stop(self):
        self.stopped = True


def test_screen_video_writes_a_real_mp4_from_the_screen(tmp_path):
    import time
    import cv2
    from assistant.show_me import ScreenVideo
    sizes = iter([(2000, 1001), (640, 480)])
    calls = []

    def capture():
        calls.append(1)
        if len(calls) == 3:
            raise OSError('screen locked')
        size = next(sizes, (2000, 1001))
        return Image.new('RGB', size, (len(calls) * 20 % 255, 0, 0))

    video = ScreenVideo(tmp_path / 'recording.mp4', capture, fps=20, max_width=1280)
    time.sleep(0.5)
    video.stop()
    reader = cv2.VideoCapture(str(tmp_path / 'recording.mp4'))
    frames = 0
    while True:
        ok, frame = reader.read()
        if not ok:
            break
        frames += 1
        assert frame.shape[:2] == (640, 1280)  # first frame scaled to 1280 wide, even height
    reader.release()
    assert frames >= 3


def test_the_recorder_films_while_it_records(tmp_path):
    FakeVideo.made = []
    recorder, clock, screen, said = recorder_for(tmp_path, video=FakeVideo)
    recorder.intercept('watch me')
    recorder.intercept('open settings')
    recorder.intercept('done recording')
    video = FakeVideo.made[0]
    assert video.path.name == 'recording.mp4' and video.stopped
    assert 'Screen recording: [recording.mp4](recording.mp4)' in (video.path.parent / 'guide.md').read_text()
    recorder.intercept('watch me')
    recorder.intercept('cancel recording')
    assert FakeVideo.made[1].stopped


def test_a_failed_video_does_not_stop_the_recording(tmp_path):
    def broken(path):
        raise RuntimeError('no codec')

    recorder, clock, screen, said = recorder_for(tmp_path, video=broken)
    assert recorder.intercept('watch me').startswith('Recording.')
    recorder.intercept('open settings')
    recorder.intercept('done recording')
    assert said[-1] == 'Your how-to is ready: How-to, 1 step.'


def test_a_guide_naming_a_repo_lands_in_its_docs_and_room_memory(tmp_path):
    FakeVideo.made = []
    hub = tmp_path / 'ascend_hub'
    hub.mkdir()
    noted = []
    recorder, clock, screen, said = recorder_for(
        tmp_path, video=FakeVideo, repos=lambda: [tmp_path / 'ascend-vision', hub],
        remember=lambda key, note: (noted.append((key, note)), True)[1])
    recorder.intercept('watch me deploy ascend hub')
    recorder.intercept('run the build')
    recorder.intercept('done recording')
    docs = hub / 'docs' / 'howto' / 'deploy-ascend-hub'
    assert (docs / 'guide.md').exists() and (docs / 'shot-01.jpg').exists()
    assert not (docs / 'recording.mp4').exists()
    assert 'recording.mp4' not in (docs / 'guide.md').read_text()
    assert noted == [('ascend_hub', 'How-to "Deploy ascend hub": docs/howto/deploy-ascend-hub/guide.md')]
    assert said[-1] == ("Your how-to is ready: deploy ascend hub, 1 step. "
                        "Also saved to ascend_hub's docs/howto and its Room memory.")


def test_no_repo_named_means_no_copy_and_a_failed_memory_is_said(tmp_path):
    hub = tmp_path / 'ascend_hub'
    hub.mkdir()
    recorder, clock, screen, said = recorder_for(tmp_path, repos=lambda: [hub], remember=lambda key, note: False)
    recorder.intercept('watch me set up the printer')
    recorder.intercept('plug it in')
    recorder.intercept('done recording')
    assert not (hub / 'docs').exists()
    assert said[-1] == 'Your how-to is ready: set up the printer, 1 step.'
    recorder.intercept('watch me fix the ascend_hub login')
    recorder.intercept('open the file')
    recorder.intercept('done recording')
    assert said[-1] == "Your how-to is ready: fix the ascend_hub login, 1 step. Also saved to ascend_hub's docs/howto."


def test_a_second_guide_for_the_same_repo_task_does_not_overwrite_the_first(tmp_path):
    hub = tmp_path / 'ascend_hub'
    hub.mkdir()
    recorder, clock, screen, said = recorder_for(tmp_path, repos=lambda: [hub])
    for _ in range(2):
        recorder.intercept('watch me deploy ascend hub')
        recorder.intercept('run the build')
        recorder.intercept('done recording')
    assert (hub / 'docs' / 'howto' / 'deploy-ascend-hub' / 'guide.md').exists()
    assert (hub / 'docs' / 'howto' / 'deploy-ascend-hub-2' / 'guide.md').exists()


def test_the_memory_note_is_capped_for_the_room():
    from assistant.show_me import memory_note
    note = memory_note('x' * 300, 'deploy')
    assert len(note) <= 200 and note.endswith(': docs/howto/deploy/guide.md')
