"""Package licenses: which packages the last Agent Room session added, and which licenses to look at twice.

Only package names leave the computer, to the public npm and PyPI registries. The severity is a first filter by
license name, not legal advice.
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
from pathlib import Path
import re
import threading
import tomllib
from urllib.parse import quote
import urllib.request

from assistant.answers import words

LOG = logging.getLogger(__name__)
SEVERITIES = ('high', 'medium', 'low')
NUMBERS = ('No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten')
_LEAD = r"(?:(?:hey|ok|okay) )?(?:vision )?(?:can you |could you |please )?"
_ASK = re.compile(rf"{_LEAD}(?:check (?:the )?(?:package |dependency )?licen[cs]es(?: for me)?(?: please)?"
                  r"|what licen[cs]es did (?:the agents|they) add)")
_REQUIREMENT = re.compile(r'^\s*([A-Za-z0-9][A-Za-z0-9._-]*)')
_MEDIUM = re.compile(r'\b(?:LGPL|MPL|EPL|CDDL|CC-BY-SA|Lesser General Public)', re.I)
_HIGH = re.compile(r'\bA?GPL(?:v?\d|\b)|General Public License|\b(?:SSPL|BUSL|Commons Clause|UNLICENSED|proprietary)\b'
                   r'|-NC\b|non-?commercial', re.I)
_EXCEPTION = re.compile(r'classpath|linking|LLVM|GCC-exception|autoconf', re.I)  # copyleft that does not spread
_SKIP_DIRS = ('node_modules/', '.venv/', 'venv/', 'vendor/', 'site-packages/')
MAX_MANIFESTS = 50
_LOW = re.compile(r'\b(?:MIT|Apache|BSD|ISC|0BSD|Unlicense|CC0|Zlib|PSF|Python|BlueOak|WTFPL)', re.I)


def _pypi(name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower()


def _requirement(line: str) -> str | None:
    line = line.split('#', 1)[0].strip()
    if not line or line.startswith('-') or '://' in line:
        return None
    found = _REQUIREMENT.match(line)
    return _pypi(found[1]) if found else None


def manifest_packages(read) -> set[tuple[str, str]]:
    """Every dependency named at one ref; ``read(path)`` returns a file's text, ``read('')`` every tracked path."""
    found: set[tuple[str, str]] = set()
    paths = [p for p in (read('') or '').splitlines()
             if p.strip() and not any(f'/{d}' in f'/{p}' for d in _SKIP_DIRS)]

    def text(path: str) -> str:
        return (read(path) or '').lstrip('\ufeff')  # a BOM would hide the first package

    manifests = [p for p in paths if p.rsplit('/', 1)[-1] in ('package.json', 'pyproject.toml')
                 or re.fullmatch(r'requirements[\w.-]*\.txt', p.rsplit('/', 1)[-1])][:MAX_MANIFESTS]
    for path in manifests:
        name = path.rsplit('/', 1)[-1]
        if name == 'package.json':
            try:
                data = json.loads(text(path))
                for section in ('dependencies', 'devDependencies', 'optionalDependencies'):
                    if isinstance(data.get(section), dict):
                        found |= {('npm', dep) for dep in data[section]}
            except (ValueError, AttributeError):
                pass
        elif name != 'pyproject.toml':
            found |= {('pypi', n) for n in map(_requirement, text(path).splitlines()) if n}
        else:
            found |= _pyproject(text(path))
    return found


def _pyproject(source: str) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    try:
        data = tomllib.loads(source)
    except (tomllib.TOMLDecodeError, TypeError):
        data = {}
    project = data.get('project') if isinstance(data.get('project'), dict) else {}
    lines = list(project.get('dependencies') or [])
    for extra in (project.get('optional-dependencies') or {}).values():
        lines += list(extra or [])
    found |= {('pypi', n) for n in map(_requirement, (str(x) for x in lines)) if n}
    poetry = data.get('tool', {}).get('poetry', {}).get('dependencies', {})
    if isinstance(poetry, dict):
        found |= {('pypi', _pypi(n)) for n in poetry if n.lower() != 'python'}
    return found


def added_packages(repo, base: str, branch: str, run_git) -> list[tuple[str, str]]:
    """Packages named on ``branch`` and not at ``base``. A ref git cannot read raises: never a false all-clear."""
    def at(ref: str):
        listing = run_git(repo, 'ls-tree', '-r', '--name-only', ref)
        if listing is None:
            raise OSError(f'git could not read {ref}')
        return lambda path: listing if not path else run_git(repo, 'show', f'{ref}:{path}')
    return sorted(manifest_packages(at(branch)) - manifest_packages(at(base)))


def license_of(ecosystem: str, name: str, fetch) -> str | None:
    if ecosystem == 'npm':
        data = fetch(f"https://registry.npmjs.org/{quote(name, safe='@')}/latest".replace('@', '%40', 1)
                     if name.startswith('@') else f'https://registry.npmjs.org/{quote(name)}/latest')
        found = data.get('license') if isinstance(data, dict) else None
        found = found.get('type') if isinstance(found, dict) else found
        return found.strip() if isinstance(found, str) and found.strip() else None
    data = fetch(f'https://pypi.org/pypi/{quote(name)}/json')
    info = data.get('info') if isinstance(data, dict) else None
    if not isinstance(info, dict):
        return None
    for key in ('license_expression', 'license'):
        value = info.get(key)
        if isinstance(value, str) and value.strip() and len(value) <= 80:  # a whole license text says nothing short
            return value.strip()
    for classifier in info.get('classifiers') or []:
        if isinstance(classifier, str) and classifier.startswith('License ::'):
            return classifier.split('::')[-1].strip()
    return None


def severity(license_name: str | None) -> str:
    if not license_name:
        return 'medium'
    options = [o.strip(' ()') for o in re.split(r'\s+OR\s+', license_name.strip()) if o.strip(' ()')]
    if len(options) > 1:  # a dual license: the user may pick the mildest one
        return min((severity(o) for o in options), key=lambda level: -SEVERITIES.index(level))
    if _MEDIUM.search(license_name):
        return 'medium'
    if _HIGH.search(license_name):
        return 'medium' if _EXCEPTION.search(license_name) else 'high'
    return 'low' if _LOW.search(license_name) else 'medium'


def _count(n: int) -> str:
    return NUMBERS[n] if n < len(NUMBERS) else str(n)


def make_fetch(timeout: float = 10.0):
    def fetch(url: str) -> dict | None:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'accept': 'application/json'}),
                                        timeout=timeout) as response:
                data = json.loads(response.read().decode('utf-8'))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None
    return fetch


class LicenseCheck:
    def __init__(self, room_data: Path, *, run_git, fetch, announce, folder: Path, clock=datetime.now, run=None,
                 added=None, lookup=None):
        self.room_data, self.announce, self.folder, self.clock = Path(room_data), announce, Path(folder), clock
        self.added = added or (lambda repo, base, branch: added_packages(repo, base, branch, run_git))
        self.lookup = lookup or (lambda ecosystem, name: license_of(ecosystem, name, fetch))
        self.run = run or (lambda job: threading.Thread(target=job, name='license-check', daemon=True).start())
        self._lock = threading.Lock()
        self._busy = False

    def _session(self):
        """The newest build session that worked in a repo: (title, repo dir, base, branch)."""
        try:
            folders = sorted((p for p in (self.room_data / 'sessions').iterdir() if p.is_dir()),
                             key=lambda p: p.name, reverse=True)[:40]
        except OSError:
            return None
        for folder in folders:
            try:
                state = json.loads((folder / 'state.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            repo = state.get('repo') if isinstance(state, dict) else None
            if (state.get('mode') != 'debate' and isinstance(repo, dict)
                    and all(isinstance(repo.get(k), str) and repo[k] for k in ('dir', 'branch', 'base'))):
                title = str(state.get('task') or 'untitled').split('\n')[0].strip()[:80]
                return title, repo['dir'], repo['base'], repo['branch']
        return None

    def intercept(self, text: str) -> str | None:
        if not _ASK.fullmatch(words(text)):
            return None
        with self._lock:
            if self._busy:
                return 'Still checking the last one.'
            session = self._session()
            if session is None:
                return "I can't find an Agent Room session with a repo to check."
            self._busy = True
        self.run(lambda: self._check(*session))
        return 'Checking the licenses now.'

    def _check(self, title: str, repo: str, base: str, branch: str) -> None:
        try:
            try:
                packages = self.added(repo, base, branch)
            except Exception as exc:
                LOG.info('License check could not read the packages (%s)', type(exc).__name__)
                self.announce("I couldn't read that session's packages.")
                return
            found = []
            for ecosystem, name in packages:
                try:
                    license_name = self.lookup(ecosystem, name)
                except Exception:
                    license_name = None
                found.append((severity(license_name), name, ecosystem, license_name))
            found.sort(key=lambda f: (SEVERITIES.index(f[0]), f[1]))
            lead = f"The last Agent Room session, '{title}', added"
            if not found:
                self.announce(f'{lead} no packages.')
                return
            self._notes(title, found)
            count = f"{len(found)} package{'' if len(found) == 1 else 's'}"
            watch = [f for f in found if f[0] != 'low']
            if not watch:
                self.announce(f'{lead} {count}, all with permissive licenses.')
                return
            spoken = '; '.join(f'{level}, {name} ' + (f'uses {lic}' if lic else 'has no license listed')
                               for level, name, _, lic in watch[:2])
            more = f' {_count(len(watch) - 2)} more in the notes.' if len(watch) > 2 else ''
            self.announce(f"{lead} {count}. {_count(len(watch))} to watch: {spoken}.{more} Notes saved.")
        finally:
            with self._lock:
                self._busy = False

    def _notes(self, title: str, found: list) -> None:
        now = self.clock()
        lines = [f'# Package licenses ({now:%Y-%m-%d %H:%M})', '', f'Agent Room session: {title}', '']
        lines += [f"- **{level.title()}:** {name} ({ecosystem}): {lic or 'no license listed'}"
                  for level, name, ecosystem, lic in found]
        lines += ['', '_A first filter by license name, not legal advice._', '']
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            path, n = self.folder / f'{now:%Y-%m-%d-%H%M}-licenses.md', 2
            while path.exists():
                path, n = self.folder / f'{now:%Y-%m-%d-%H%M}-licenses-{n}.md', n + 1
            path.write_text('\n'.join(lines), encoding='utf-8')
        except OSError as exc:
            LOG.warning('License notes not saved (%s)', type(exc).__name__)
