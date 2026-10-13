from datetime import date, datetime, timedelta

import pytest

from integrations.github_coach import (GitHubCoach, afternoon_nudge, evening_nudge, fetch_calendar,
                                       parse_calendar, progress, report)

TODAY = date(2026, 10, 13)


def page(counts: dict[date, int]) -> str:
    cells, tips = [], []
    for index, (day, count) in enumerate(sorted(counts.items())):
        cell_id = f'contribution-day-component-{index % 7}-{index // 7}'
        if index % 2:   # both attribute orders appear in real pages
            cells.append(f'<td tabindex="0" id="{cell_id}" data-date="{day.isoformat()}" data-level="1"></td>')
        else:
            cells.append(f'<td tabindex="0" data-date="{day.isoformat()}" id="{cell_id}" data-level="1"></td>')
        label = 'No contributions' if count == 0 else f'{count:,} contribution{"" if count == 1 else "s"}'
        tips.append(f'<tool-tip id="t{index}" for="{cell_id}" class="sr-only">{label} on October 1st.</tool-tip>')
    return '<table>' + ''.join(cells) + '</table>' + ''.join(tips)


def days(*values):
    """values[0] is today, values[1] yesterday, ..."""
    return {TODAY - timedelta(days=offset): value for offset, value in enumerate(values)}


def test_calendar_parsing_handles_counts_commas_and_no_contributions():
    counts = days(8, 5, 0, 1, 1200)
    assert parse_calendar(page(counts)) == counts
    with pytest.raises(ValueError):
        parse_calendar('<html>layout changed</html>')


def test_progress_streak_and_week_trend():
    p = progress(days(0, 3, 2, 1, 0, 4, 4, 0, 1, 1, 1, 1, 1, 1), TODAY)
    assert p.today == 0 and p.streak == 3                 # today not done yet, so count from yesterday
    assert p.average == pytest.approx(14 / 7) and p.behind == 2
    assert (p.this_week, p.last_week) == (14, 6)


def test_wording_for_behind_ahead_and_streak_rescue():
    behind = progress(days(1, 5, 5, 5, 5, 5, 5, 5), TODAY)
    assert afternoon_nudge(behind) == ("GitHub check: 1 contribution today; your 7-day average is 5, "
                                       "so you're 4 behind. A small commit counts.")
    assert evening_nudge(behind) is None                  # something was pushed today
    nothing = progress(days(0, 2, 2, 2), TODAY)
    assert evening_nudge(nothing) == 'No contributions yet today. One push keeps your 3-day streak alive.'
    ahead = progress(days(8, 1, 1, 1, 1, 1, 0, 5), TODAY)
    assert afternoon_nudge(ahead) is None
    text = report(ahead)
    assert text.startswith("Today you have 8 contributions. That's above your 7-day average of 1.4.")
    assert 'Streak: 6 days.' in text


def test_username_is_checked_before_any_request():
    with pytest.raises(ValueError):
        fetch_calendar('bad name/../x')


class Now:
    def __init__(self, hour):
        self.value = datetime(2026, 10, 13, hour, 0)

    def __call__(self):
        return self.value


def coach(monkeypatch, hour, counts, said, fetches):
    monkeypatch.setattr('integrations.github_coach.threading.Thread',
                        lambda target, args=(), **_kw: type('T', (), {'start': lambda self: target(*args)})())
    now = Now(hour)

    def fetcher(username):
        fetches.append(username)
        return counts

    return GitHubCoach('RIll-cmd', announce=said.append, fetcher=fetcher, now=now), now


def test_reminders_fire_once_at_their_hour_and_only_when_needed(monkeypatch):
    said, fetches = [], []
    c, now = coach(monkeypatch, 14, days(0, 3, 3, 3, 3, 3, 3, 3), said, fetches)
    c.maybe_remind(True)
    assert said == [] and fetches == []                    # before 3 PM nothing happens
    now.value = now.value.replace(hour=15)
    c.maybe_remind(False)
    assert said == []                                      # owner away or Vision asleep
    c.maybe_remind(True)
    c.maybe_remind(True)
    assert len(said) == 1 and said[0].startswith('GitHub check: 0 contributions today')
    now.value = now.value.replace(hour=20)
    c.maybe_remind(True)
    assert len(said) == 2 and 'streak alive' in said[1]
    c.maybe_remind(True)
    assert len(said) == 2 and len(fetches) == 1            # one fetch, then the 30-minute cache


def test_late_start_gives_only_the_evening_reminder(monkeypatch):
    said, fetches = [], []
    c, _ = coach(monkeypatch, 21, days(0, 1), said, fetches)
    c.maybe_remind(True)
    c.maybe_remind(True)
    assert len(said) == 1 and 'streak alive' in said[0]


def test_questions_get_the_report_and_failures_are_plain(monkeypatch):
    said, fetches = [], []
    c, _ = coach(monkeypatch, 10, days(8, 1), said, fetches)
    assert c.intercept('how many commits did I make today?').startswith('Today you have 8 contributions.')
    assert c.intercept("what's the weather?") is None

    def broken(_name):
        raise OSError('offline')

    offline = GitHubCoach('RIll-cmd', announce=said.append, fetcher=broken)
    assert offline.intercept('how is my github streak?') == 'I could not read your GitHub activity right now. Try again in a bit.'
