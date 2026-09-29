"""Task-bound upload staging and bounded managed downloads for browser tasks."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_DOWNLOAD_TOTAL_BYTES = 500 * 1024 * 1024
MAX_DOWNLOAD_COUNT = 100
_SAFE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')


class FileLimitExceeded(ValueError):
    """A selected file or downloaded response exceeded its configured limit."""


@dataclass(frozen=True)
class BrowserFile:
    filename: str
    path: Path
    size: int
    sha256: str


class BrowserTaskFiles:
    """Only opaque file IDs cross the model boundary; paths stay inside this adapter."""

    def __init__(self, root: str | Path | None = None, *, max_upload_bytes=MAX_UPLOAD_BYTES,
                 max_download_bytes=MAX_DOWNLOAD_BYTES,
                 max_download_total_bytes=MAX_DOWNLOAD_TOTAL_BYTES,
                 max_download_count=MAX_DOWNLOAD_COUNT):
        if root is None:
            local = os.environ.get('LOCALAPPDATA')
            if os.name != 'nt' or not local:
                raise RuntimeError('Browser file handoff requires Windows owner-local storage.')
            root = Path(local) / 'AscendVision' / 'browser' / 'files'
        if (type(max_upload_bytes) is not int or not 1 <= max_upload_bytes <= MAX_UPLOAD_BYTES
                or type(max_download_bytes) is not int or not 1 <= max_download_bytes <= MAX_DOWNLOAD_BYTES
                or type(max_download_total_bytes) is not int or not 1 <= max_download_total_bytes <= MAX_DOWNLOAD_TOTAL_BYTES
                or type(max_download_count) is not int or not 1 <= max_download_count <= MAX_DOWNLOAD_COUNT):
            raise ValueError('browser file limits exceed their safe maximum')
        self._root = Path(root)
        self._max_upload_bytes = max_upload_bytes
        self._max_download_bytes = max_download_bytes
        self._max_download_total_bytes = max_download_total_bytes
        self._max_download_count = max_download_count

    @staticmethod
    def owner_key(owner: str) -> str:
        if not isinstance(owner, str) or not owner.strip() or len(owner) > 128:
            raise ValueError('file owner is invalid')
        return hashlib.sha256(owner.casefold().encode('utf-8')).hexdigest()

    @staticmethod
    def _validate_id(value: str, name: str) -> str:
        if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
            raise ValueError(f'{name} is invalid')
        return value

    @staticmethod
    def _safe_filename(value: str) -> str:
        if not isinstance(value, str):
            value = ''
        name = value.replace('\\', '/').split('/')[-1]
        name = ''.join(char for char in name if char.isalnum() or char in ' ._()-')
        name = name.strip(' .')[:120]
        return name or 'browser-file'

    def _task_dir(self, owner: str, task_id: str) -> Path:
        return self._root / self.owner_key(owner) / self._validate_id(task_id, 'task_id')

    def stage_upload(self, owner: str, task_id: str, filename: str, stream) -> tuple[str, BrowserFile]:
        directory = self._task_dir(owner, task_id)
        directory.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(18)
        if not _SAFE_ID.fullmatch(token):
            token = secrets.token_hex(18)
        path = directory / f'{token}.upload'
        temporary = directory / f'.{token}.tmp'
        digest = hashlib.sha256()
        size = 0
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, 'wb') as target:
                while True:
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self._max_upload_bytes:
                        raise FileLimitExceeded('The selected upload exceeds the 10 MiB limit.')
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            safe_filename = self._safe_filename(filename)
            for existing_metadata_path in directory.glob('*.upload.json'):
                existing = json.loads(existing_metadata_path.read_text(encoding='utf-8'))
                if (existing.get('filename') == safe_filename and existing.get('size') == size
                        and existing.get('sha256') == digest.hexdigest()):
                    temporary.unlink(missing_ok=True)
                    existing_token = existing_metadata_path.name.removesuffix('.upload.json')
                    existing_file = directory / f'{existing_token}.upload'
                    return existing_token, BrowserFile(
                        safe_filename, existing_file, size, digest.hexdigest(),
                    )
                raise FileExistsError('Only one explicitly selected upload is allowed per browser task.')
            os.replace(temporary, path)
            item = BrowserFile(safe_filename, path, size, digest.hexdigest())
            metadata_path = directory / f'{token}.upload.json'
            metadata_tmp = directory / f'.{token}.json.tmp'
            metadata_tmp.write_text(json.dumps({
                'filename': item.filename, 'size': item.size, 'sha256': item.sha256,
            }), encoding='utf-8')
            os.replace(metadata_tmp, metadata_path)
            return token, item
        except Exception:
            for candidate in (temporary, path, directory / f'{token}.upload.json',
                              directory / f'.{token}.json.tmp'):
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass
            raise

    def upload(self, owner: str, task_id: str, token: str) -> BrowserFile:
        self._validate_id(token, 'file token')
        directory = self._task_dir(owner, task_id)
        if not directory.is_dir():
            raise PermissionError('This selected file is not available to the task owner.')
        metadata = json.loads((directory / f'{token}.upload.json').read_text(encoding='utf-8'))
        path = directory / f'{token}.upload'
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError('The selected upload is no longer available.')
        return BrowserFile(metadata['filename'], path, metadata['size'], metadata['sha256'])

    def cancel_upload(self, owner: str, task_id: str, token: str) -> bool:
        self._validate_id(token, 'file token')
        directory = self._task_dir(owner, task_id)
        removed = False
        for suffix in ('.upload', '.upload.json'):
            try:
                (directory / f'{token}{suffix}').unlink()
                removed = True
            except FileNotFoundError:
                pass
        return removed

    def save_download(self, owner: str, task_id: str, filename: str, stream, *, cancelled=None) -> BrowserFile:
        directory = self._root / 'downloads'
        directory.mkdir(parents=True, exist_ok=True)
        existing_files = [path for path in directory.iterdir() if path.is_file() and not path.is_symlink()]
        existing_bytes = sum(path.stat().st_size for path in existing_files)
        if len(existing_files) >= self._max_download_count or existing_bytes >= self._max_download_total_bytes:
            raise FileLimitExceeded('The managed browser Downloads folder reached its storage limit.')
        safe_name = self._safe_filename(filename)
        stem, suffix = os.path.splitext(safe_name)
        temporary = directory / f'.{secrets.token_hex(12)}.download.tmp'
        digest = hashlib.sha256()
        size = 0
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, 'wb') as target:
                while True:
                    if cancelled is not None and cancelled():
                        raise InterruptedError('Browser download was cancelled.')
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self._max_download_bytes:
                        raise FileLimitExceeded('The browser download exceeds the 25 MiB limit.')
                    if existing_bytes + size > self._max_download_total_bytes:
                        raise FileLimitExceeded('The managed browser Downloads folder reached its storage limit.')
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            candidate = directory / safe_name
            suffix_index = 1
            while True:
                try:
                    # A same-volume hard link is atomic and fails rather than
                    # replacing a collision on both Windows and POSIX.
                    os.link(temporary, candidate)
                    temporary.unlink()
                    break
                except FileExistsError:
                    candidate = directory / f'{stem} ({suffix_index}){suffix}'
                    suffix_index += 1
            return BrowserFile(candidate.name, candidate, size, digest.hexdigest())
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def cleanup_task(self, owner: str, task_id: str) -> None:
        directory = self._task_dir(owner, task_id)
        if not directory.exists():
            return
        for path in directory.iterdir():
            if path.is_file() and path.name.endswith(('.upload', '.upload.json')):
                path.unlink(missing_ok=True)
