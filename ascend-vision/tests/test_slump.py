from datetime import date, datetime, timedelta, timezone
import re

from assistant.slump import (NUDGE_TEXT, Ladder, SlumpCoach, SlumpStore, assess, backfill, focus_days,
                             room_session_days, sample_minute)

TODAY = date(2026, 10, 29)


def series(baseline, recent, *, baseline_days=14, recent_days=3):
    """Values for the baseline window, then the 3 days before TODAY."""
    days = {}
    for offset in range(1, recent_days + 1):
        days[TODAY - timedelta(days=offset)] = recent
    for offset in range(4, 4 + baseline_days):
        days[TODAY - timedelta(days=offset)] = baseline
    return days


def test_a_drop_needs_the_recent_average_below_half_the_baseline():
    assert assess({'commits': series(4, 1)}, TODAY).fired == ('commits',)
    assert assess({'commits': series(4, 3)}, TODAY).fired == ()
    assert assess({'commits': series(4, 1)}, TODAY).signals['commits'] == (1, 4)


def test_two_signals_make_a_slump():
    both = assess({'commits': series(4, 1), 'active_minutes': series(300, 60)}, TODAY)
    assert both.slump and set(both.fired) == {'commits', 'active_minutes'}
    one = assess({'commits': series(4, 1), 'active_minutes': series(300, 280)}, TODAY)
    assert not one.slump


def test_late_nights_fire_over_thirty_and_baseline_plus_thirty():
    assert assess({'late_minutes': series(10, 45)}, TODAY).fired == ('late_minutes',)
    assert assess({'late_minutes': series(10, 35)}, TODAY).fired == ()
    assert assess({'late_minutes': series(0, 25)}, TODAY).fired == ()


def test_distraction_fires_over_thirty_and_twice_the_baseline():
    assert assess({'distraction_minutes': series(20, 50)}, TODAY).fired == ('distraction_minutes',)
    assert assess({'distraction_minutes': series(20, 35)}, TODAY).fired == ()


def test_metrics_without_seven_baseline_days_are_ignored():
    result = assess({'commits': series(4, 1, baseline_days=6)}, TODAY)
    assert 'commits' not in result.signals
    assert result.learning and not result.slump
    assert not assess({'commits': series(4, 1, baseline_days=7)}, TODAY).learning


def test_missing_recent_days_are_not_zero():
    result = assess({'focus_minutes': series(60, 0, recent_days=0)}, TODAY)
    assert result.fired == ()
    assert 'focus_minutes' not in result.signals
    assert not result.learning


def test_today_is_not_part_of_the_recent_window():
    days = series(4, 4)
    days[TODAY] = 0
    assert assess({'commits': days}, TODAY).fired == ()


def test_store_adds_and_survives_reopen(tmp_path):
    path = tmp_path / 'slump.db'
    SlumpStore(path).add(TODAY, 'active_minutes', 1)
    store = SlumpStore(path)
    store.add(TODAY, 'active_minutes', 1)
    store.put(TODAY, 'commits', 3)
    store.put(TODAY, 'commits', 5)
    assert store.days(TODAY) == {'active_minutes': {TODAY: 2}, 'commits': {TODAY: 5}}


def test_store_prunes_after_ninety_days(tmp_path):
    store = SlumpStore(tmp_path / 'slump.db')
    store.put(TODAY - timedelta(days=91), 'commits', 1)
    store.put(TODAY - timedelta(days=90), 'commits', 2)
    store.prune(TODAY)
    assert store.days(TODAY - timedelta(days=200)) == {'commits': {TODAY - timedelta(days=90): 2}}


def test_store_keeps_the_ladder(tmp_path):
    store = SlumpStore(tmp_path / 'slump.db')
    assert store.ladder() == Ladder()
    ladder = Ladder(step=2, last_day=TODAY, slump_days=2, paused_until=TODAY + timedelta(days=3), accepted=True)
    store.save_ladder(ladder)
    assert SlumpStore(tmp_path / 'slump.db').ladder() == ladder


def _focus_db(path, rows):
    import sqlite3
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY, start_time TEXT NOT NULL, end_time TEXT, mode TEXT NOT NULL)")
        db.executemany('INSERT INTO sessions (start_time, end_time, mode) VALUES (?, ?, ?)', rows)
    db.close()


def _local(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute).astimezone()


def test_minutes_count_active_time_and_late_time(tmp_path):
    store = SlumpStore(tmp_path / 'slump.db')
    sample_minute(store, 'input_active', datetime(2026, 10, 29, 2, 10))
    sample_minute(store, 'input_active', datetime(2026, 10, 29, 14, 0))
    sample_minute(store, 'input_idle', datetime(2026, 10, 29, 3, 0))
    sample_minute(store, 'input_idle', datetime(2026, 10, 30, 10, 0))
    assert store.days(TODAY) == {
        'active_minutes': {TODAY: 2, TODAY + timedelta(days=1): 0},
        'late_minutes': {TODAY: 1, TODAY + timedelta(days=1): 0},
    }


def test_backfill_puts_github_and_room_days(tmp_path):
    store = SlumpStore(tmp_path / 'slump.db')
    yesterday = TODAY - timedelta(days=1)
    calendar = {yesterday: 3, TODAY - timedelta(days=2): 0}
    room = {yesterday: 2}
    backfill(store, today=TODAY, calendar=calendar, room_sessions=room)
    days = store.days(TODAY - timedelta(days=10))
    assert days['commits'] == calendar
    assert days['room_sessions'] == room


def test_an_unreachable_source_leaves_stored_days_alone(tmp_path):
    store = SlumpStore(tmp_path / 'slump.db')
    yesterday = TODAY - timedelta(days=1)
    store.put(yesterday, 'commits', 5)
    store.put(yesterday, 'room_sessions', 2)
    backfill(store, today=TODAY, calendar=None, room_sessions=None,
             focus_db=tmp_path / 'missing.db', distraction_db=tmp_path / 'missing2.db')
    assert store.days(yesterday) == {'commits': {yesterday: 5}, 'room_sessions': {yesterday: 2}}


def test_focus_minutes_sum_focus_sessions_per_local_day(tmp_path):
    path = tmp_path / 'phone_watch.db'
    yesterday = TODAY - timedelta(days=1)
    start = _local(yesterday, 9)
    _focus_db(path, [
        (start.isoformat(), (start + timedelta(minutes=25)).isoformat(), 'focus'),
        ((start + timedelta(hours=2)).isoformat(), (start + timedelta(hours=2, minutes=50)).isoformat(), 'focus'),
        ((start + timedelta(hours=4)).isoformat(), (start + timedelta(hours=6)).isoformat(), 'background'),
        ((start + timedelta(hours=7)).isoformat(), None, 'focus'),
    ])
    assert focus_days(path) == {yesterday: 75}
    assert focus_days(tmp_path / 'missing.db') == {}


def test_distraction_days_fill_quiet_days_with_zero(tmp_path):
    import sqlite3
    path = tmp_path / 'distractions.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE distraction_time (day TEXT NOT NULL, site TEXT NOT NULL, seconds REAL NOT NULL)')
        db.executemany('INSERT INTO distraction_time VALUES (?, ?, ?)', [
            ((TODAY - timedelta(days=3)).isoformat(), 'youtube.com', 600),
            ((TODAY - timedelta(days=3)).isoformat(), 'reddit.com', 300),
        ])
    db.close()
    store = SlumpStore(tmp_path / 'slump.db')
    backfill(store, today=TODAY, distraction_db=path)
    assert store.days(TODAY - timedelta(days=5))['distraction_minutes'] == {
        TODAY - timedelta(days=3): 15, TODAY - timedelta(days=2): 0, TODAY - timedelta(days=1): 0}


def test_room_session_days_use_local_dates_and_fill_quiet_days():
    yesterday = TODAY - timedelta(days=1)
    late = _local(yesterday, 23, 30)
    payload = {'sessions': [
        {'createdAt': late.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')},
        {'createdAt': _local(TODAY - timedelta(days=3), 10).astimezone(timezone.utc).isoformat()},
        {'createdAt': 'garbage'},
        {},
    ]}
    assert room_session_days(payload, today=TODAY) == {
        TODAY - timedelta(days=3): 1, TODAY - timedelta(days=2): 0, yesterday: 1}
    assert room_session_days({'sessions': []}, today=TODAY) == {}
    assert room_session_days({'nope': 1}, today=TODAY) == {}


class Clock:
    def __init__(self):
        self.moment = None

    def __call__(self):
        return self.moment

    def at(self, day_offset, hour, minute=0):
        day = TODAY + timedelta(days=day_offset)
        self.moment = datetime(day.year, day.month, day.day, hour, minute)
        return self

    def later(self, seconds):
        self.moment += timedelta(seconds=seconds)
        return self


def seed(store, *, high=None, low=None):
    """A usual stretch before TODAY-3, then a low stretch from TODAY-3 to TODAY+5 with an hour at the PC."""
    high = high or {'commits': 4, 'active_minutes': 300}
    low = low or {'commits': 1, 'active_minutes': 60}
    for offset in range(-20, 6):
        values = high if offset < -3 else {**low, 'active_minutes': max(60, low.get('active_minutes', 60))}
        for metric, value in values.items():
            store.put(TODAY + timedelta(days=offset), metric, value)


def coach_for(path, clock, **kwargs):
    store = SlumpStore(path / 'slump.db')
    said = []

    def announce(text, on_said=None):  # speaks at once, like an empty pacer queue
        said.append(text)
        if on_said:
            on_said()

    return store, said, SlumpCoach(store, announce=announce, clock=clock, **kwargs)


def test_check_in_waits_for_daytime_and_thirty_active_minutes(tmp_path):
    clock = Clock().at(0, 7, 30)
    store, said, coach = coach_for(tmp_path, clock)
    seed(store, high={'commits': 4, 'room_sessions': 2, 'active_minutes': 300},
         low={'commits': 1, 'room_sessions': 0})
    store.put(TODAY, 'active_minutes', 20)
    coach.tick(True)
    clock.at(0, 9)
    coach.tick(True)
    assert said == []
    store.put(TODAY, 'active_minutes', 30)
    coach.tick(False)
    assert said == []
    coach.tick(True)
    assert len(said) == 1 and said[0].endswith('Want a small plan for today?')
    assert 'fewer commits' in said[0] and 'fewer Agent Room sessions' in said[0]
    clock.at(1, 21, 5)
    coach.tick(True)
    assert len(said) == 1


def test_at_most_one_unprompted_line_per_day(tmp_path):
    clock = Clock().at(0, 10)
    store, said, coach = coach_for(tmp_path, clock)
    seed(store)
    for _ in range(5):
        coach.tick(True)
        clock.later(3600)
    assert len(said) == 1


def test_yes_to_the_check_in_offers_one_small_step_from_what_dropped(tmp_path):
    clock = Clock().at(0, 10)
    store, said, coach = coach_for(tmp_path, clock)
    seed(store)
    coach.tick(True)
    assert 'one small commit on anything' in coach.intercept('Yes please')
    assert coach.intercept('sure').startswith('Good.')
    assert store.ladder().accepted


def test_a_focus_plan_checks_in_after_twenty_five_minutes(tmp_path):
    clock = Clock().at(0, 10)
    store, said, coach = coach_for(tmp_path, clock)
    seed(store, high={'room_sessions': 2, 'active_minutes': 300}, low={'room_sessions': 0, 'active_minutes': 60})
    coach.tick(True)
    assert 'focus block' in coach.intercept('yes')
    assert coach.intercept('okay') == "Good. I'll check in after twenty-five minutes."
    clock.later(24 * 60)
    coach.tick(True)
    assert len(said) == 1
    clock.later(60)
    coach.tick(True)
    assert said[-1] == "That's twenty-five minutes. Nice work."


def test_the_next_slump_day_offers_the_step_without_asking(tmp_path):
    clock = Clock().at(0, 10)
    store, said, coach = coach_for(tmp_path, clock)
    seed(store)
    coach.tick(True)
    clock.at(1, 10)
    coach.tick(True)
    assert len(said) == 2 and 'one small commit on anything' in said[1]
    assert coach.intercept('yes').startswith('Good.')


def test_the_third_slump_day_without_acceptance_sends_one_phone_nudge(tmp_path):
    pushed = []
    clock = Clock().at(0, 10)
    store, said, coach = coach_for(tmp_path, clock, notify=pushed.append)
    seed(store)
    for day in range(5):
        clock.at(day, 10)
        coach.tick(True)
    assert pushed == ['Ascend: a small plan is waiting for you on the PC.'] == [NUDGE_TEXT]
    assert len(said) == 2


def test_no_nudge_when_phone_nudge_is_off_or_a_plan_was_accepted(tmp_path):
    clock = Clock().at(0, 10)
    store, said, coach = coach_for(tmp_path / 'off', clock, notify=None)
    seed(store)
    for day in range(4):
        clock.at(day, 10)
        coach.tick(True)
    assert len(said) == 2

    pushed = []
    clock = Clock().at(0, 10)
    store, said, coach = coach_for(tmp_path / 'accepted', clock, notify=pushed.append)
    seed(store)
    coach.tick(True)
    coach.intercept('yes')
    coach.intercept('yes')
    for day in range(1, 4):
        clock.at(day, 10)
        coach.tick(True)
    assert pushed == [] and len(said) == 1


def test_two_calm_days_reset_the_ladder(tmp_path):
    clock = Clock().at(0, 10)
    store, said, coach = coach_for(tmp_path, clock)
    seed(store)
    coach.tick(True)
    assert store.ladder().step == 1
    for offset in range(-3, 6):
        store.put(TODAY + timedelta(days=offset), 'commits', 4)
    clock.at(1, 10)
    coach.tick(True)
    assert store.ladder().step == 1
    clock.at(2, 10)
    coach.tick(True)
    assert store.ladder().step == 0 and len(said) == 1


def test_not_now_pauses_three_days(tmp_path):
    clock = Clock().at(0, 10)
    store, said, coach = coach_for(tmp_path, clock)
    seed(store)
    coach.tick(True)
    assert coach.intercept('Not now.') == "Okay. I'll leave it for a few days."
    for day in (1, 2):
        clock.at(day, 10)
        coach.tick(True)
    assert len(said) == 1
    clock.at(3, 10)
    coach.tick(True)
    assert len(said) == 2


def test_a_late_yes_is_not_taken(tmp_path):
    clock = Clock().at(0, 10)
    store, said, coach = coach_for(tmp_path, clock)
    seed(store)
    coach.tick(True)
    clock.later(121)
    assert coach.intercept('yes') is None
    assert coach.intercept('not now') is None
    assert coach.intercept('what is the weather') is None


def test_how_am_i_doing_answers_any_time(tmp_path):
    clock = Clock().at(0, 10)
    store, said, coach = coach_for(tmp_path, clock)
    assert coach.intercept('Vision, how am I doing?') == "I'm still learning your normal week."
    seed(store)
    answer = coach.intercept('how am i doing lately')
    assert 'commits' in answer and '4' in answer and '1' in answer


def test_unprompted_lines_hold_no_numbers(tmp_path):
    clock = Clock().at(0, 10)
    pushed = []
    store, said, coach = coach_for(tmp_path, clock, notify=pushed.append)
    seed(store, high={'commits': 4, 'active_minutes': 300, 'late_minutes': 0, 'focus_minutes': 90,
                      'room_sessions': 2, 'distraction_minutes': 5},
         low={'commits': 0, 'active_minutes': 140, 'late_minutes': 90, 'focus_minutes': 0,
              'room_sessions': 0, 'distraction_minutes': 120})
    for day in range(4):
        clock.at(day, 10)
        coach.tick(True)
    store.save_ladder(Ladder())
    seed(store, high={'commits': 4, 'room_sessions': 2, 'active_minutes': 300},
         low={'commits': 4, 'room_sessions': 0, 'active_minutes': 60, 'late_minutes': 0})
    clock.at(4, 10)
    coach.tick(True)
    coach.intercept('yes')
    coach.intercept('yes')
    clock.later(25 * 60)
    coach.tick(True)
    assert len(said) >= 4 and pushed
    for line in said + pushed:
        assert not re.search(r'\d', line), line


def test_tick_never_waits_for_a_slow_backfill(tmp_path):
    import threading
    release, done = threading.Event(), threading.Event()

    def sources():
        release.wait(5)
        return {'calendar': {TODAY - timedelta(days=1): 3}}

    clock = Clock().at(0, 10)
    store, said, coach = coach_for(tmp_path, clock, backfill_sources=sources)
    coach.tick(True)
    coach.tick(True)
    assert 'commits' not in store.days(TODAY - timedelta(days=5))
    release.set()
    coach.backfill_thread.join(5)
    assert store.days(TODAY - timedelta(days=5))['commits'] == {TODAY - timedelta(days=1): 3}


def test_a_failing_backfill_source_is_skipped(tmp_path):
    clock = Clock().at(0, 10)
    store, said, coach = coach_for(tmp_path, clock, backfill_sources=lambda: 1 / 0)
    coach.tick(True)
    coach.backfill_thread.join(5)
    assert store.days(TODAY - timedelta(days=5)) == {}


def test_not_now_resets_the_ladder_so_no_push_follows_the_pause(tmp_path):
    pushed = []
    clock = Clock().at(0, 10)
    store, said, coach = coach_for(tmp_path, clock, notify=pushed.append)
    seed(store)
    coach.tick(True)
    clock.at(1, 10)
    coach.tick(True)
    assert coach.intercept('not now') == "Okay. I'll leave it for a few days."
    for day in range(2, 5):
        clock.at(day, 10)
        coach.tick(True)
    assert pushed == []
    assert len(said) == 3 and said[2].endswith('Want a small plan for today?')


def test_the_offer_window_opens_when_the_line_is_spoken(tmp_path):
    clock = Clock().at(0, 10)
    store = SlumpStore(tmp_path / 'slump.db')
    queued = []
    coach = SlumpCoach(store, announce=lambda text, on_said=None: queued.append(on_said), clock=clock)
    seed(store)
    coach.tick(True)
    assert coach.intercept('yes') is None
    clock.later(110)
    queued[0]()
    clock.later(100)
    assert 'one small commit on anything' in coach.intercept('yes')


def test_no_check_in_while_the_owner_is_away_from_the_keyboard(tmp_path):
    activity = ['input_idle']
    clock = Clock().at(0, 10)
    store, said, coach = coach_for(tmp_path, clock, sample=lambda: activity[0])
    seed(store)
    coach.tick(True)
    assert said == []
    activity[0] = 'input_active'
    clock.later(60)
    coach.tick(True)
    assert len(said) == 1


def test_late_night_minutes_do_not_count_toward_the_daytime_thirty(tmp_path):
    clock = Clock().at(0, 10)
    store, said, coach = coach_for(tmp_path, clock)
    seed(store)
    store.put(TODAY, 'active_minutes', 60)
    store.put(TODAY, 'late_minutes', 40)
    coach.tick(True)
    assert said == []
    store.put(TODAY, 'active_minutes', 70)
    coach.tick(True)
    assert len(said) == 1


def test_natural_answers_to_the_offer_count_as_yes(tmp_path):
    for answer in ('sounds good', 'okay sounds good', "yes let's try it", "let's do it", 'Vision, yes'):
        clock = Clock().at(0, 10)
        store, said, coach = coach_for(tmp_path / answer.replace(' ', '_').replace("'", '').replace(',', ''), clock)
        seed(store)
        coach.tick(True)
        assert coach.intercept(answer), answer
    clock = Clock().at(0, 10)
    store, said, coach = coach_for(tmp_path / 'negated', clock)
    seed(store)
    coach.tick(True)
    assert coach.intercept("yes but not today") != "Here's one small step for today: one small commit on anything. Sound good?"
