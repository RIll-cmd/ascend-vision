"""Show me once: "Vision, watch me" records what the owner says and the screen, then writes a how-to guide.

Narration comes through the local chat (voice or typed), so no second microphone is opened. Screenshots stay on
this computer; only the transcript text may go to the AI, which turns it into clean steps.
"""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
import re
import shutil
import threading

from PIL import Image, ImageChops, ImageStat

from assistant.answers import words

LOG = logging.getLogger(__name__)
SILENT_ACK = '📝'  # shown in chat for each narrated line, never spoken
CHANGE_CHECK_SECONDS = 15
CHANGE_THRESHOLD = 8.0  # mean grey-level difference of a small thumbnail
MAX_WIDTH = 1600
_LEAD = r"(?:(?:hey|ok|okay) )?(?:vision )?"
# "watch me" alone, or followed by a task ("watch me set up the printer"), never "watch me play this song".
_VERBS = (r"(?:do|set ?up|fix|make|install|configure|deploy|build|update|upgrade|renew|create|clean|back ?up|reset"
          r"|change|add|remove|test|run|release|publish|export|import|connect|move|write|send|prepare|check|turn"
          r"|enable|disable|sign|log|order|pay|book|file|print|scan|edit|rename|restore|repair|replace)")
_START = re.compile(rf"{_LEAD}(?:watch me|show me once|record (?:a )?how to|record how i)"
                    rf"(?: (?:how to |how i )?{_VERBS}\b.*)?")
_START_PHRASE = re.compile(r"(?:watch me|show me once|record (?:a )?how[- ]?to|record how i)[\s,:]*"
                           r"(?:(?:how to|how i)\s+)?", re.I)
_STOP = re.compile(r"\b(?:done|stop|finish|end)(?:ed|ped)? (?:the )?recording\b")
_CANCEL = re.compile(r"\bcancel (?:the )?recording\b")
SHORT_COMMAND = 6  # words; a longer line with "stop recording" in it is narration
MAX_AI_CHARS = 12000  # a longer transcript is written line by line rather than cut
WRITE_PROMPT = (
    'You turn a spoken walkthrough into a clear how-to guide. The user message lists numbered lines the person said '
    'while doing the task. Reply with JSON only: {"title": short title, "summary": one sentence, '
    '"steps": [{"text": one clear instruction, "from": [line numbers it comes from]}], '
    '"checklist": [short checks that the task worked]}. Use 1 to 30 steps in order and at most 8 checklist items. '
    'Use only what was said; drop filler and false starts; never invent steps.')


def _one_line(text: str, limit: int) -> str:
    """Plain text on one line: narration must not add headings, images or links to the guide."""
    flat = ' '.join(str(text).split())[:limit]
    return flat.replace('<', '&lt;').replace('![', '!\\[').replace('](', ']\\(')


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def fallback_steps(lines: list[str]) -> dict:
    return {'title': None, 'summary': '', 'checklist': [],
            'steps': [{'text': _sentence(line.strip()), 'from': [i]} for i, line in enumerate(lines, 1)]}


def parse_steps(answer, line_count: int) -> dict | None:
    """The AI's guide if its steps are well formed, else None; a bad title, summary or checklist is only dropped."""
    if not isinstance(answer, dict) or not isinstance(answer.get('steps'), list) or not 1 <= len(answer['steps']) <= 30:
        return None
    steps = []
    for step in answer['steps']:
        if not isinstance(step, dict):
            return None
        text, lines = step.get('text'), step.get('from')
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 400 or not isinstance(lines, list) or not lines:
            return None
        if any(type(n) is not int or not 1 <= n <= line_count for n in lines):
            return None
        steps.append({'text': text.strip(), 'from': lines})
    title, summary, checklist = answer.get('title'), answer.get('summary'), answer.get('checklist')
    return {'title': title.strip() if isinstance(title, str) and 0 < len(title.strip()) <= 120 else None,
            'summary': summary.strip() if isinstance(summary, str) and len(summary) <= 300 else '',
            'checklist': [c.strip() for c in checklist if isinstance(c, str) and 0 < len(c.strip()) <= 200][:8]
            if isinstance(checklist, list) else [],
            'steps': steps}


def make_writer(router):
    """Ask the configured AI for the steps; only the transcript text is sent."""
    def write(lines: list[str]):
        numbered = '\n'.join(f'{i}. {line}' for i, line in enumerate(lines, 1))
        return router.generate_structured_response(numbered, system_prompt=WRITE_PROMPT,
                                                   task='reasoning', max_tokens=1500)
    return write


def render_guide(title: str, summary: str, steps: list[dict], checklist: list[str], shots: dict[int, list[str]],
                 start_shots: list[str] = ()) -> str:
    parts = [f'# {_one_line(_sentence(title), 120)}', '']
    if summary:
        parts += [_one_line(summary, 300), '']
    for shot in start_shots:
        parts += [f'![Start]({shot})', '']
    for number, step in enumerate(steps, 1):
        parts.append(f"{number}. {_one_line(step['text'], 400)}")
        images = [shot for line in step['from'] for shot in shots.get(line, [])]
        for index, shot in enumerate(images):
            parts.append(f"   ![Step {number}{', later' if index else ''}]({shot})")
        parts.append('')
    if checklist:
        parts += ['## Check', ''] + [f'- [ ] {_one_line(item, 200)}' for item in checklist] + ['']
    return '\n'.join(parts)


def _slug(title: str) -> str:
    return '-'.join(re.findall(r'[a-z0-9]+', title.lower()))[:60].strip('-') or 'how-to'


class HowToRecorder:
    def __init__(self, folder: Path, *, announce, capture, write_steps=None, clock=datetime.now, run=None,
                 max_seconds: int = 1800, max_shots: int = 60):
        self.folder = Path(folder)
        self.announce, self.capture, self.write_steps, self.clock = announce, capture, write_steps, clock
        self.run = run or (lambda job: threading.Thread(target=job, name='how-to-writer', daemon=True).start())
        self.max_seconds, self.max_shots = max_seconds, max_shots
        self._lock = threading.Lock()
        self._reset()

    def _reset(self) -> None:
        self.recording = False
        self._dir: Path | None = None
        self._title: str | None = None
        self._lines: list[str] = []
        self._shots: dict[int, list[str]] = {}  # line number (0 = before the first line) -> files
        self._count = 0
        self._thumb = None
        self._started = self._last_check = None

    def intercept(self, text: str) -> str | None:
        said = words(text)
        with self._lock:
            if not self.recording:
                if not _START.fullmatch(said):
                    return None
                phrase = _START_PHRASE.search(text)
                title = text[phrase.end():].strip(' .!?,"\'') if phrase else ''
                try:
                    self._begin(title or None)
                except OSError as exc:
                    LOG.warning('How-to folder not writable (%s)', type(exc).__name__)
                    self._reset()
                    return "I couldn't start recording: the how-to folder is not writable."
                return "Recording. Talk me through each step, and say 'done recording' when you finish."
            short = len(said.split()) <= SHORT_COMMAND
            if short and _CANCEL.search(said):
                shutil.rmtree(self._dir, ignore_errors=True)
                self._reset()
                return 'Recording thrown away.'
            if short and _STOP.search(said):
                if not self._lines:
                    shutil.rmtree(self._dir, ignore_errors=True)
                    self._reset()
                    return 'Nothing was said, so I kept nothing.'
                self._finish()
                return 'Got it. Writing the guide now.'
            self._lines.append(text.strip())
            self._shoot(len(self._lines))
            self._draft()
            return SILENT_ACK

    def _draft(self) -> None:
        """A plain guide on disk after every line, so quitting Vision mid-recording loses nothing."""
        guide = fallback_steps(self._lines)
        try:
            (self._dir / 'guide.md').write_text(
                render_guide(self._title or 'How-to', '', guide['steps'], [], self._shots, self._shots.get(0, [])),
                encoding='utf-8')
        except OSError as exc:
            LOG.debug('How-to draft not saved (%s)', type(exc).__name__)

    def tick(self) -> None:
        with self._lock:
            if not self.recording:
                return
            now = self.clock()
            if (now - self._started).total_seconds() >= self.max_seconds:
                minutes = self.max_seconds // 60
                self.announce('I stopped recording after thirty minutes.' if minutes == 30
                              else f'I stopped recording after {minutes} minutes.')
                self._finish()
                return
            if (now - self._last_check).total_seconds() >= CHANGE_CHECK_SECONDS:
                self._shoot(len(self._lines), only_if_changed=True)

    def _begin(self, title: str | None) -> None:
        self._title = title.strip() if title else None
        self._started = self._last_check = self.clock()
        base = f"{self._started:%Y-%m-%d}-{_slug(self._title or 'how-to')}"
        self._dir, n = self.folder / base, 2
        while self._dir.exists():
            self._dir, n = self.folder / f'{base}-{n}', n + 1
        self._dir.mkdir(parents=True)
        self.recording = True  # only once the folder exists
        self._shoot(0)

    def _shoot(self, line: int, only_if_changed: bool = False) -> None:
        self._last_check = self.clock()
        if self._count >= self.max_shots:
            return
        try:
            image = self.capture().convert('RGB')
            thumb = image.resize((32, 18)).convert('L')
            if only_if_changed and self._thumb is not None:
                if ImageStat.Stat(ImageChops.difference(thumb, self._thumb)).mean[0] < CHANGE_THRESHOLD:
                    return
            self._thumb = thumb
            if image.width > MAX_WIDTH:
                image = image.resize((MAX_WIDTH, round(image.height * MAX_WIDTH / image.width)), Image.LANCZOS)
            self._count += 1
            name = f'shot-{self._count:02d}.jpg'
            image.save(self._dir / name, 'JPEG', quality=80)
            self._shots.setdefault(line, []).append(name)
        except Exception as exc:  # A locked screen must not stop the recording or lose the words.
            LOG.debug('How-to screenshot skipped (%s)', type(exc).__name__)

    def _finish(self) -> None:
        folder, title, lines, shots = self._dir, self._title, list(self._lines), dict(self._shots)
        self._reset()
        self.run(lambda: self._write(folder, title, lines, shots))

    def _write(self, folder: Path, title: str | None, lines: list[str], shots: dict[int, list[str]]) -> None:
        guide = None
        if self.write_steps is not None and lines and sum(len(line) + 6 for line in lines) <= MAX_AI_CHARS:
            try:
                guide = parse_steps(self.write_steps(lines), len(lines))
            except Exception as exc:
                LOG.info('How-to written without AI (%s)', type(exc).__name__)
        guide = guide or fallback_steps(lines)
        title = title or guide['title'] or 'How-to'
        try:
            (folder / 'guide.md').write_text(
                render_guide(title, guide['summary'], guide['steps'], guide['checklist'], shots, shots.get(0, [])),
                encoding='utf-8')
        except OSError as exc:
            LOG.warning('How-to not saved (%s)', type(exc).__name__)
            self.announce("I couldn't save the how-to.")
            return
        count = len(guide['steps'])
        self.announce(f"Your how-to is ready: {title}, {count} step{'' if count == 1 else 's'}.")
