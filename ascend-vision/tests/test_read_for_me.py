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


@pytest.mark.parametrize('answer', [None, 'not json', {'summary': ''}, {'summary': 'x', 'red_flags': 'many'},
                                    {'summary': 'x', 'red_flags': [{'severity': 'urgent', 'text': 'y'}]},
                                    {'summary': 'x', 'red_flags': [{'severity': 'high', 'text': 'y'}] * 9}])
def test_a_bad_model_reply_is_not_trusted(tmp_path, answer):
    assert parse_reading(answer) is None
    reader, calls, said, jobs = reader_for(tmp_path, answer=answer)
    reader.intercept('read this for me')
    finish(jobs)
    assert said == ["I couldn't read that one. Try again, or bring it closer."]
    assert reader.intercept('read this for me') == 'Reading it now.', 'not stuck busy'


def test_a_failing_model_or_missing_key(tmp_path):
    reader, calls, said, jobs = reader_for(tmp_path, answer=PermissionError('no key'))
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
