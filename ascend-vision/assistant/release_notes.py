"""Release notes factory: "Vision, write release notes for ascend hub" turns the commits since last time into a
grouped changelog, a short what's-new list and a post the owner can share.

Repos are only read. The notes are written under Vision's data folder; nothing is tagged, pushed or posted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import threading

from assistant.answers import words

LOG = logging.getLogger(__name__)
SEP, END = '\x1f', '\x1e'
MAX_COMMITS = 200
RECENT = '14 days ago'
GROUPS = (('New', 'feat'), ('Fixes', 'fix'), ('Docs', 'docs'), ('Other', 'other'))
_CONVENTIONAL = re.compile(r'^(\w+)(?:\([^)]*\))?!?:\s*(.+)$')
_ASK = re.compile(r"(?:(?:hey|ok|okay) )?(?:vision )?(?:can you )?(?:write|make|draft) (?:the |some )?release notes"
                  r"(?: for (?P<repo>[a-z' ]{1,40}?))?(?: please)?")
LAST_FEW = 20  # a repo with no notes yet and nothing in two weeks still gets its latest commits
STORY_PROMPT = (
    'You turn commit subjects into release notes for the people who use the app, not for developers. Reply with '
    'JSON only: {"whats_new": [1 to 5 short plain sentences about what people can do now or what got better], '
    '"post": one friendly first-person post under 280 characters, at most two hashtags}. Use only what the commits '
    'say; never invent features; skip internal work such as refactors, tests and docs unless it changes something '
    'people notice.')


@dataclass(frozen=True)
class Commit:
    sha: str
    kind: str  # feat | fix | docs | other
    subject: str
    date: str


def parse_log(text: str) -> list[Commit]:
    commits = []
    for record in text.split(END):
        fields = record.strip('\n').split(SEP)
        if len(fields) < 3 or not fields[0].strip():
            continue
        # A subject holding the separator byte keeps its commit: the middle fields are the subject.
        sha, subject, date = fields[0].strip(), ' '.join(fields[1:-1]).strip(), fields[-1].strip()
        found = _CONVENTIONAL.match(subject)
        if found:
            kind = found[1].lower() if found[1].lower() in ('feat', 'fix', 'docs') else 'other'
            subject = found[2].strip()
        else:
            kind = 'other'
        commits.append(Commit(sha, kind, subject, date))
    return commits


def group(commits: list[Commit]) -> dict[str, list[Commit]]:
    return {title: [c for c in commits if c.kind == kind] for title, kind in GROUPS}


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text[1:2].islower() else text


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def fallback_story(name: str, commits: list[Commit]) -> dict:
    picks = [c.subject for c in commits if c.kind == 'feat'] or [c.subject for c in commits if c.kind == 'fix']
    post = (f'New in {name}: {_join([_lower_first(p) for p in picks[:3]])}.' if picks
            else f'Small updates to {name}.')
    return {'whats_new': [_sentence(p) for p in picks[:5]], 'post': post[:280]}


def parse_story(answer) -> dict | None:
    if not isinstance(answer, dict):
        return None
    items, post = answer.get('whats_new'), answer.get('post')
    if not isinstance(items, list):
        return None
    items = items[:5]  # a long list is trimmed, not thrown away
    if not items or any(not isinstance(i, str) or not 0 < len(i.strip()) <= 200 for i in items):
        return None
    if not isinstance(post, str) or not 0 < len(post.strip()) <= 280:
        return None
    return {'whats_new': [i.strip() for i in items], 'post': post.strip()}


def make_story_writer(router):
    def write(name: str, commits: list[Commit]):
        listing = '\n'.join(f'- {c.kind}: {c.subject}' for c in commits)
        return router.generate_structured_response(f'App: {name}\n{listing}'[:12000], system_prompt=STORY_PROMPT,
                                                   task='reasoning', max_tokens=800)
    return write


def _plain(text, limit: int) -> str:
    flat = ' '.join(str(text).split())[:limit]
    return flat.replace('<', '&lt;').replace('![', '!\\[').replace('](', ']\\(').lstrip('#').strip()


def render_notes(name: str, now: datetime, commits: list[Commit], story: dict) -> str:
    parts = [f'# {_plain(name, 80)}: release notes, {now:%Y-%m-%d}', '']
    if story['whats_new']:
        parts += ["## What's new", ''] + [f'- {_plain(item, 200)}' for item in story['whats_new']] + ['']
    parts += ['## Post', '', _plain(story['post'], 280), '', '## Changelog', '']
    for title, items in group(commits).items():
        if items:
            parts += [f'### {title}', ''] + [f'- {_plain(c.subject, 200)} ({_plain(c.sha, 12)})' for c in items] + ['']
    return '\n'.join(parts)


def _spoken(folder_name: str) -> str:
    return ' '.join(re.split(r'[-_]+', folder_name)).strip()


def _key(name: str) -> str:
    return re.sub(r'[^a-z0-9]', '', name.lower())


class ReleaseNotes:
    def __init__(self, *, repos, run_git, write_story=None, folder, announce, clock=datetime.now, run=None):
        self.repos, self.run_git, self.write_story = repos, run_git, write_story
        self.folder, self.announce, self.clock = Path(folder), announce, clock
        self.run = run or (lambda job: threading.Thread(target=job, name='release-notes', daemon=True).start())
        self._lock = threading.Lock()
        self._busy = False

    def _newest(self, repo: Path) -> int:
        stamp = (self.run_git(repo, 'log', '-1', '--format=%ct') or '').strip()
        return int(stamp) if stamp.isdigit() else 0

    def _pick(self, name: str | None) -> Path | None:
        repos = [Path(r) for r in self.repos()]
        if name:
            exact = [r for r in repos if _key(r.name) == _key(name)]
            # "vision" means ascend-vision: a part of the name is enough when nothing matches exactly.
            repos = exact or ([r for r in repos if _key(name) in _key(r.name)] if len(_key(name)) >= 3 else [])
        return max(repos, key=self._newest, default=None)

    def intercept(self, text: str) -> str | None:
        asked = _ASK.fullmatch(words(text))
        if not asked:
            return None
        with self._lock:
            if self._busy:
                return 'Still writing the last ones.'
            repo = self._pick(asked['repo'])
            if repo is None:  # "release notes for the new login page" is chat, not a repo
                return None if asked['repo'] else "I don't see any repos."
            self._busy = True
        self.run(lambda: self._write(repo))
        return f'Writing the release notes for {_spoken(repo.name)}.'

    def _state(self) -> dict:
        try:
            data = json.loads((self.folder / 'state.json').read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _range(self, repo: Path, last: str | None, head: str) -> list[str]:
        if (isinstance(last, str) and re.fullmatch(r'[0-9a-f]{7,64}', last)
                and self.run_git(repo, 'merge-base', '--is-ancestor', last, head) is not None):
            return [f'{last}..{head}']
        tag = (self.run_git(repo, 'describe', '--tags', '--abbrev=0', head) or '').strip()
        if tag:
            return [f'{tag}..{head}']
        return [f'--since={RECENT}', head]

    def _write(self, repo: Path) -> None:
        name = _spoken(repo.name)
        try:
            self._write_notes(repo, name)
        except Exception as exc:
            LOG.warning('Release notes failed (%s)', type(exc).__name__)
            self.announce(f"I couldn't write the release notes for {name}.")
        finally:
            with self._lock:
                self._busy = False

    def _write_notes(self, repo: Path, name: str) -> None:
        state = self._state()
        # Fixed before the slow AI call, so a commit made meanwhile is left for next time, not skipped.
        head = (self.run_git(repo, 'rev-parse', 'HEAD') or '').strip()
        if not head:
            self.announce(f'Nothing new in {name} since the last notes.')
            return
        last = state.get(repo.name)
        span = self._range(repo, last, head)
        fmt = f'--format=%h{SEP}%s{SEP}%cI{END}'
        commits = parse_log(self.run_git(repo, 'log', '--no-merges', f'--max-count={MAX_COMMITS}', fmt, *span, '--')
                            or '')
        if not commits and last is None and span[0].startswith('--since'):
            commits = parse_log(self.run_git(repo, 'log', '--no-merges', f'--max-count={LAST_FEW}', fmt, head, '--')
                                or '')
        if not commits:
            self.announce(f'Nothing new in {name} since the last notes.')
            return
        story = None
        if self.write_story is not None:
            try:
                story = parse_story(self.write_story(name, commits))
            except Exception as exc:
                LOG.info('Release notes written without AI (%s)', type(exc).__name__)
        story = story or fallback_story(name, commits)
        now = self.clock()
        self.folder.mkdir(parents=True, exist_ok=True)
        base = f'{repo.name}-{now:%Y-%m-%d}'
        path, n = self.folder / f'{base}.md', 2
        while path.exists():
            path, n = self.folder / f'{base}-{n}.md', n + 1
        path.write_text(render_notes(name, now, commits, story), encoding='utf-8')
        if head:
            state[repo.name] = head
            temporary = self.folder / 'state.json.tmp'
            temporary.write_text(json.dumps(state, indent=1), encoding='utf-8')
            os.replace(temporary, self.folder / 'state.json')
        groups = group(commits)
        new, fixes = len(groups['New']), len(groups['Fixes'])
        self.announce(f"Release notes for {name} are ready: {len(commits)} change{'' if len(commits) == 1 else 's'}, "
                      f"{f'{new} new' if new else 'nothing new'} and "
                      f"{f'{fixes} fix' + ('' if fixes == 1 else 'es') if fixes else 'no fixes'}.")
