from datetime import datetime, timedelta
import os

from PIL import Image
import pytest

from assistant.read_for_me import DocumentReader, latest_pdf, parse_reading, render_notes, spoken_answer

NOW = datetime(2026, 10, 30, 14, 5)
LEASE = {'kind': 'lease', 'summary': 'A one-year flat lease at 900 a month. Utilities are extra.',
         'red_flags': [{'severity': 'low', 'text': 'pets need written approval'},
                       {'severity': 'high', 'text': 'it renews every year unless you cancel 60 days before'},
                       {'severity': 'medium', 'text': 'a 5 percent late fee'}],
         'dates': ['cancel by 2027-08-31']}


def reader_for(tmp_path, answer=LEASE, camera=True, downloads=None):
    calls, said, jobs = [], [], []

    def ask_model(kind, data, mime):
        calls.append((kind, mime, len(data)))
        if isinstance(answer, Exception):
            raise answer
        return answer
    reader = DocumentReader(
        ask_model=ask_model, screen=lambda: Image.new('RGB', (3000, 1500), 'white'),
        camera=(lambda: Image.new('RGB', (640, 480), 'gray')) if camera else (lambda: None),
        downloads=downloads or tmp_path / 'Downloads', announce=said.append, folder=tmp_path / 'readings',
        clock=lambda: NOW, run=jobs.append)
    return reader, calls, said, jobs


def finish(jobs):
    while jobs:
        jobs.pop(0)()


def test_reading_the_screen_speaks_the_summary_and_worst_flags_and_keeps_notes(tmp_path):
    reader, calls, said, jobs = reader_for(tmp_path)
    assert reader.intercept('what is the weather') is None
    assert reader.intercept('Vision, read this for me.') == 'Reading it now.'
    assert reader.intercept('read my screen') == 'Still reading the last one.'
    finish(jobs)
    assert calls[0][:2] == ('screen', 'image/jpeg')
    assert said == ['A one-year flat lease at 900 a month. Two things to watch: high, it renews every year unless '
                    'you cancel 60 days before; medium, a 5 percent late fee. One more in the notes. '
                    "I'm not a lawyer, so for anything big, ask one."]
    (notes,) = (tmp_path / 'readings').iterdir()
    assert notes.name == '2026-10-30-1405-lease.md'
    text = notes.read_text(encoding='utf-8')
    assert '- **High:** it renews every year' in text and '- cancel by 2027-08-31' in text
    assert 'Not legal advice.' in text and 'Source: the screen' in text


def test_a_held_page_comes_from_the_camera(tmp_path):
    reader, calls, said, jobs = reader_for(tmp_path, answer={'kind': 'letter', 'summary': 'A school trip form.',
                                                             'red_flags': [], 'dates': []})
    assert reader.intercept("read what I'm holding") == 'Reading it now.'
    finish(jobs)
    assert calls[0][0] == 'camera' and said == ['A school trip form. Nothing stood out as a red flag.']
    blind, *_ = reader_for(tmp_path / 'b', camera=False)
    assert blind.intercept('read this paper') == "I can't see the camera right now."


def test_the_newest_recent_pdf_in_downloads(tmp_path):
    downloads = tmp_path / 'Downloads'
    downloads.mkdir()
    old = downloads / 'old.pdf'
    old.write_bytes(b'%PDF-1.4 old')
    os.utime(old, (0, (NOW - timedelta(days=10)).timestamp()))
    newer = downloads / 'lease.pdf'
    newer.write_bytes(b'%PDF-1.4 lease')
    os.utime(newer, (0, (NOW - timedelta(hours=1)).timestamp()))
    (downloads / 'photo.png').write_bytes(b'x')
    assert latest_pdf(downloads, NOW) == newer
    huge = downloads / 'huge.pdf'
    with open(huge, 'wb') as file:
        file.truncate(21 * 1024 * 1024)
    assert latest_pdf(downloads, NOW) == newer
    reader, calls, said, jobs = reader_for(tmp_path, downloads=downloads)
    assert reader.intercept('read my latest download') == 'Reading it now.'
    finish(jobs)
    assert calls == [('lease.pdf', 'application/pdf', len(b'%PDF-1.4 lease'))]
    assert 'Source: lease.pdf' in next((tmp_path / 'readings').iterdir()).read_text(encoding='utf-8')


def test_no_pdf_says_so(tmp_path):
    reader, *_ = reader_for(tmp_path)
    assert reader.intercept('check my last PDF') == "I don't see a PDF in Downloads from the last week."


@pytest.mark.parametrize('answer', [None, 'not json', {'summary': ''}, {'summary': 'x', 'red_flags': 'many'}])
def test_a_bad_model_reply_is_not_trusted(tmp_path, answer):
    assert parse_reading(answer) is None
    reader, calls, said, jobs = reader_for(tmp_path, answer=answer)
    reader.intercept('read this for me')
    finish(jobs)
    assert said == ["I couldn't read that one. Try again, or bring it closer."]
    assert reader.intercept('read this for me') == 'Reading it now.', 'not stuck busy'


def test_a_failing_model_or_missing_key(tmp_path):
    from assistant.read_for_me import MissingKey
    reader, calls, said, jobs = reader_for(tmp_path, answer=MissingKey())
    reader.intercept('read this for me')
    finish(jobs)
    assert said == ['Reading needs a Gemini key.']
    reader, calls, said, jobs = reader_for(tmp_path, answer=TimeoutError('slow'))
    reader.intercept('read this for me')
    finish(jobs)
    assert said == ["I couldn't read that one. Try again, or bring it closer."]


def test_images_are_sent_small():
    from assistant.read_for_me import _jpeg
    with Image.open(__import__('io').BytesIO(_jpeg(Image.new('RGB', (3000, 1500))))) as image:
        assert image.size == (1600, 800)


def test_notes_stay_plain_text():
    notes = render_notes({'kind': 'terms', 'summary': '# Safe!\n![x](http://evil)', 'dates': [],
                          'red_flags': [{'severity': 'high', 'text': '<img src=x>'}]}, 'the screen', NOW)
    assert '\n# Safe' not in notes and '![x]' not in notes and '<img' not in notes


def test_spoken_answer_without_flags_or_with_one():
    one = {'kind': 'invoice', 'summary': 'An invoice for 120. Due Friday.', 'red_flags': [
        {'severity': 'medium', 'text': 'a 30 dollar late fee'}], 'dates': []}
    assert spoken_answer(one) == 'An invoice for 120. One thing to watch: medium, a 30 dollar late fee.'
    capital = {**one, 'red_flags': [{'severity': 'high', 'text': 'The plan renews.'},
                                    {'severity': 'low', 'text': 'USD only.'}]}
    assert spoken_answer(capital).endswith('high, the plan renews; low, USD only.')


def test_one_odd_flag_does_not_lose_the_reading():
    reading = parse_reading({'kind': 'Lease Agreement', 'summary': 'A lease.', 'red_flags': [
        {'severity': 'High', 'text': 'renews by itself'}, {'severity': 'critical', 'text': 'odd'}, 'junk']
        + [{'severity': 'low', 'text': f'small {n}'} for n in range(9)]})
    assert reading['red_flags'][0] == {'severity': 'high', 'text': 'renews by itself'}
    assert len(reading['red_flags']) == 8
    assert spoken_answer(reading).endswith("I'm not a lawyer, so for anything big, ask one.")


def test_a_too_big_newest_pdf_is_refused_not_swapped(tmp_path):
    downloads = tmp_path / 'Downloads'
    downloads.mkdir()
    older = downloads / 'invoice.pdf'
    older.write_bytes(b'%PDF small')
    os.utime(older, (0, (NOW - timedelta(hours=2)).timestamp()))
    big = downloads / 'lease.pdf'
    with open(big, 'wb') as file:
        file.truncate(21 * 1024 * 1024)
    os.utime(big, (0, (NOW - timedelta(hours=1)).timestamp()))
    reader, *_ = reader_for(tmp_path, downloads=downloads)
    assert reader.intercept('read my latest download') == 'The newest PDF, lease.pdf, is too big for me to read.'


def test_an_empty_or_still_downloading_pdf_is_skipped(tmp_path):
    downloads = tmp_path / 'Downloads'
    downloads.mkdir()
    ready = downloads / 'ready.pdf'
    ready.write_bytes(b'%PDF ok')
    os.utime(ready, (0, (NOW - timedelta(hours=2)).timestamp()))
    for name in ('report.pdf', 'report.pdf.part', 'empty.pdf'):
        (downloads / name).write_bytes(b'x' if name.endswith('.part') else b'')
        os.utime(downloads / name, (0, (NOW - timedelta(minutes=5)).timestamp()))
    (downloads / 'report.pdf').write_bytes(b'%PDF partial')
    os.utime(downloads / 'report.pdf', (0, (NOW - timedelta(minutes=5)).timestamp()))
    assert latest_pdf(downloads, NOW) == ready


def test_the_pdf_name_is_said_with_the_answer(tmp_path):
    downloads = tmp_path / 'Downloads'
    downloads.mkdir()
    lease = downloads / 'lease.pdf'
    lease.write_bytes(b'%PDF lease')
    os.utime(lease, (0, (NOW - timedelta(hours=1)).timestamp()))
    reader, calls, said, jobs = reader_for(tmp_path, downloads=downloads)
    reader.intercept('read my latest download')
    finish(jobs)
    assert said[0].startswith('lease.pdf: A one-year flat lease')


def test_a_file_read_error_is_not_called_a_missing_key(tmp_path):
    reader, calls, said, jobs = reader_for(tmp_path, answer=PermissionError('file locked'))
    reader.intercept('read this for me')
    finish(jobs)
    assert said == ["I couldn't read that one. Try again, or bring it closer."]


def test_a_tiny_placeholder_frame_counts_as_no_camera(tmp_path):
    reader, calls, said, jobs = reader_for(tmp_path)
    reader.camera = lambda: Image.new('RGB', (1, 1))
    assert reader.intercept('read this paper') == "I can't see the camera right now."


def test_no_document_keeps_no_notes(tmp_path):
    reader, calls, said, jobs = reader_for(tmp_path, answer={'kind': 'none', 'summary': "I can't see a document.",
                                                             'red_flags': [], 'dates': []})
    reader.intercept('read this for me')
    finish(jobs)
    assert said == ["I can't see a document."] and not (tmp_path / 'readings').exists()


def test_a_failed_screenshot_is_said_plainly(tmp_path):
    reader, *_ = reader_for(tmp_path)

    def locked():
        raise OSError('screen locked')
    reader.screen = locked
    assert reader.intercept('read this for me') == "I couldn't take a screenshot right now."
    assert reader.intercept('read this for me') == "I couldn't take a screenshot right now.", 'not stuck busy'


@pytest.mark.parametrize('phrase', ['can you read this for me', 'read this for me please', 'read the latest pdf',
                                    'read this page for me', 'Read what I’m holding'])
def test_natural_phrasings(tmp_path, phrase):
    reader, *_ = reader_for(tmp_path)
    assert reader.intercept(phrase) is not None
