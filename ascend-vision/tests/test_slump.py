from datetime import date, timedelta

from assistant.slump import Ladder, SlumpStore, assess

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
