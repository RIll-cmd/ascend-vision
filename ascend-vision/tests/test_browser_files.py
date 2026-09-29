import io

import pytest

from browser.files import BrowserTaskFiles, FileLimitExceeded


def test_upload_is_task_bound_and_client_filename_cannot_escape_storage(tmp_path):
    files = BrowserTaskFiles(tmp_path, max_upload_bytes=8)
    token, item = files.stage_upload('owner-1', 'task-1', '..\\..\\private.txt', io.BytesIO(b'1234'))
    assert item.filename == 'private.txt'
    assert item.size == 4
    assert item.path.parent == tmp_path / files.owner_key('owner-1') / 'task-1'
    assert item.path.read_bytes() == b'1234'
    with pytest.raises(PermissionError):
        files.upload('owner-2', 'task-1', token)
    with pytest.raises(PermissionError):
        files.upload('owner-1', 'task-2', token)


def test_upload_size_limit_and_cancel_remove_staged_content(tmp_path):
    files = BrowserTaskFiles(tmp_path, max_upload_bytes=4)
    with pytest.raises(FileLimitExceeded):
        files.stage_upload('owner-1', 'task-1', 'large.bin', io.BytesIO(b'12345'))
    token, _ = files.stage_upload('owner-1', 'task-1', 'small.bin', io.BytesIO(b'1234'))
    assert files.cancel_upload('owner-1', 'task-1', token) is True
    with pytest.raises(FileNotFoundError):
        files.upload('owner-1', 'task-1', token)


def test_upload_retry_with_same_task_and_bytes_reuses_opaque_token(tmp_path):
    files = BrowserTaskFiles(tmp_path)
    first_token, _ = files.stage_upload('owner-1', 'task-1', 'report.txt', io.BytesIO(b'report'))
    retry_token, _ = files.stage_upload('owner-1', 'task-1', 'report.txt', io.BytesIO(b'report'))
    assert retry_token == first_token
    with pytest.raises(FileExistsError):
        files.stage_upload('owner-1', 'task-1', 'other.txt', io.BytesIO(b'different'))


def test_download_folder_has_a_total_storage_limit(tmp_path):
    files = BrowserTaskFiles(tmp_path, max_download_total_bytes=5)
    files.save_download('owner-1', 'task-1', 'one.bin', io.BytesIO(b'1234'))
    with pytest.raises(FileLimitExceeded, match='storage limit'):
        files.save_download('owner-1', 'task-2', 'two.bin', io.BytesIO(b'12'))


def test_download_sanitizes_name_avoids_overwrite_and_cleans_cancelled_partial(tmp_path):
    files = BrowserTaskFiles(tmp_path, max_download_bytes=8)
    first = files.save_download('owner-1', 'task-1', '..\\report.txt', io.BytesIO(b'first'))
    second = files.save_download('owner-1', 'task-2', 'report.txt', io.BytesIO(b'second'))
    assert first.filename == 'report.txt'
    assert second.filename == 'report (1).txt'
    assert first.path.read_bytes() == b'first'
    assert second.path.read_bytes() == b'second'

    with pytest.raises(FileLimitExceeded):
        files.save_download('owner-1', 'task-3', 'too-large', io.BytesIO(b'123456789'))

    class DiskFull:
        def __init__(self): self.calls = 0
        def read(self, size):
            self.calls += 1
            if self.calls > 1: raise OSError('disk full')
            return b'partial'

    with pytest.raises(OSError, match='disk full'):
        files.save_download('owner-1', 'task-4', 'disk-full.bin', DiskFull())
    download_dir = tmp_path / 'downloads'
    assert not list(download_dir.glob('*.tmp'))
    with pytest.raises(InterruptedError, match='cancelled'):
        files.save_download('owner-1', 'task-5', 'cancelled.bin', io.BytesIO(b'content'), cancelled=lambda: True)
    assert not list(download_dir.glob('*.tmp'))
