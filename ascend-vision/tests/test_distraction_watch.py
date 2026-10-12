from datetime import date

import pytest

from integrations.distraction_watch import DistractionStore, DistractionWatch, match_site

SITES = ('YouTube', 'Instagram', 'X', 'Reddit', 'Netflix')


@pytest.mark.parametrize('title, expected', [
    ('Funny cats compilation - YouTube - Google Chrome', 'YouTube'),
    ('(3) YouTube - Opera', 'YouTube'),
    ('Home / X - Google Chrome', 'X'),
    ('(12) Instagram • Photos and videos - Google Chrome', 'Instagram'),
    ('Reddit - Dive into anything - Brave', 'Reddit'),
    ('Netflix - Microsoft Edge', 'Netflix'),
    ('youtube-dl docs - GitHub - Google Chrome', None),          # a word inside a part is not the site
    ('Xcode release notes - Google Chrome', None),
    ('Python docs: pathlib - Google Chrome', None),
    ('', None),
])
def test_site_is_matched_only_as_a_whole_title_part(title, expected):
    assert match_site(title, SITES) == expected


class Probe:
    def __init__(self):
        self.front = None

    def foreground(self):
        return self.front


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(store=None, focus=True, accept=True):
    probe, clock, nudges = Probe(), Clock(), []
    state = {'focus': focus, 'accept': accept}

    def nudge(site):
        nudges.append((clock.now, site))
        return state['accept']

    watch = DistractionWatch(probe, SITES, is_focus=lambda: state['focus'], nudge=nudge, store=store,
                             grace_seconds=60, repeat_seconds=300, poll_seconds=2, retry_seconds=10, clock=clock)
    return watch, probe, clock, nudges, state


def run(watch, clock, seconds, step=2):
    for _ in range(int(seconds / step)):
        clock.now += step
        watch.tick()


def test_nudges_after_the_grace_period_then_every_repeat_while_on_the_site():
    watch, probe, clock, nudges, _ = make()
    probe.front = ('chrome.exe', 'Cats - YouTube - Google Chrome')
    watch.tick()
    run(watch, clock, 58)
    assert nudges == []                      # a quick look is fine
    run(watch, clock, 4)
    assert [site for _, site in nudges] == ['YouTube']
    run(watch, clock, 290)
    assert len(nudges) == 1
    run(watch, clock, 12)
    assert len(nudges) == 2                  # five minutes later, still there


def test_leaving_the_site_resets_the_grace_period():
    watch, probe, clock, nudges, _ = make()
    probe.front = ('chrome.exe', 'Cats - YouTube - Google Chrome')
    watch.tick()
    run(watch, clock, 40)
    probe.front = ('Code.exe', 'main.py - Visual Studio Code')
    run(watch, clock, 10)
    probe.front = ('chrome.exe', 'Cats - YouTube - Google Chrome')
    run(watch, clock, 40)
    assert nudges == []


def test_nothing_happens_while_paused_or_outside_a_browser():
    watch, probe, clock, nudges, state = make(focus=False)
    probe.front = ('chrome.exe', 'Cats - YouTube - Google Chrome')
    run(watch, clock, 120)
    assert nudges == [] and watch.current_site() is None
    state['focus'] = True
    probe.front = ('Spotify.exe', 'YouTube - Spotify')        # not a browser
    run(watch, clock, 120)
    assert nudges == []


def test_a_nudge_dropped_while_vision_was_speaking_is_retried_soon():
    watch, probe, clock, nudges, state = make(accept=False)
    probe.front = ('msedge.exe', 'Home / X - Microsoft Edge')
    watch.tick()
    run(watch, clock, 62)
    assert len(nudges) == 1
    state['accept'] = True
    run(watch, clock, 10)
    assert len(nudges) == 2                  # retried after 10 s, not 5 min


def test_time_on_a_site_is_saved_per_day_without_titles(tmp_path):
    store = DistractionStore(tmp_path / 'distractions.db')
    watch, probe, clock, _, _ = make(store=store)
    probe.front = ('opera.exe', 'Secret project plans - Reddit - Opera')
    watch.tick()
    run(watch, clock, 120)
    today = date.today()
    totals = DistractionStore.totals(tmp_path / 'distractions.db', today, today)
    assert totals[0]['site'] == 'Reddit' and totals[0]['seconds'] == pytest.approx(120, abs=2)
    assert b'Secret project' not in (tmp_path / 'distractions.db').read_bytes()
    assert DistractionStore.totals(tmp_path / 'missing.db', today, today) == []


def test_site_name_reaches_the_roast_wording():
    from feedback_router import get_expression_prompt_context, get_fallback_expression_reply
    assert 'YouTube' in get_expression_prompt_context('distraction:YouTube')
    assert 'YouTube' in get_fallback_expression_reply('distraction:YouTube')


def test_settings_accept_an_edited_list_and_reject_bad_values(tmp_path):
    from config import load_config
    path = tmp_path / 'config.yaml'
    path.write_text('distraction_watch:\n  grace_seconds: 30\n  sites: [YouTube, " Twitch "]\n', encoding='utf-8')
    config = load_config(path).distraction_watch
    assert config.enabled is True and config.sites == ('YouTube', 'Twitch') and config.grace_seconds == 30
    path.write_text('distraction_watch:\n  sites: YouTube\n', encoding='utf-8')
    with pytest.raises(ValueError):
        load_config(path)
