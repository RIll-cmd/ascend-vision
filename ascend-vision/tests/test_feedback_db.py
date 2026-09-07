from datetime import datetime, timedelta, timezone

import pytest

from db import Database

NOW = datetime(2026, 9, 6, 4, tzinfo=timezone.utc)


def test_context_uses_real_counts_and_only_four_fields(tmp_path):
    with Database(tmp_path / 'test.db') as db:
        background = db.start_session('background', NOW)
        db.start_event(background, NOW, .9)
        focus = db.switch_session(background, 'focus', NOW + timedelta(minutes=1))
        db.start_event(focus, NOW + timedelta(minutes=2), .8)
        event = db.start_event(focus, NOW + timedelta(minutes=3), .9)
        sid, mode, payload = db.feedback_context(event)
        assert (sid, mode) == (focus, 'focus')
        assert payload == {'pickups_today': 3, 'pickups_this_session': 2,
            'session_duration_minutes': 2., 'time_of_day': (NOW + timedelta(minutes=3)).astimezone().strftime('%H:%M')}


def test_roast_is_persisted_once_only_for_focus(tmp_path):
    with Database(tmp_path / 'test.db') as db:
        background = db.start_session('background', NOW)
        first = db.start_event(background, NOW, .9)
        with pytest.raises(ValueError, match='focus'):
            db.save_roast(first, 'No background speech.')
        focus = db.switch_session(background, 'focus', NOW + timedelta(seconds=1))
        second = db.start_event(focus, NOW + timedelta(seconds=2), .9)
        db.save_roast(second, 'Your phone can wait.')
        db.save_roast(second, 'Your phone can wait.')
        with pytest.raises(ValueError, match='different'):
            db.save_roast(second, 'A second roast.')
        assert db.connection.execute('SELECT roast_text FROM phone_events WHERE id=?', (second,)).fetchone()[0] == 'Your phone can wait.'
