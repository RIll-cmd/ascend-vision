import io
import json
from datetime import datetime

import pytest

from integrations.weather import (Place, PlaceForecast, WeatherCoach, fetch, going_out_advice, summary,
                                  worth_asking)

PLACES = [Place('Sampaloc', 14.6088, 120.9961), Place('San Miguel', 15.1423, 120.9757)]


def hours(chances):
    return tuple(enumerate(chances))


RAINY = PlaceForecast('Sampaloc', 30.5, 31.6, 37.0, 8.1, hours([10] * 14 + [98, 100, 97, 92, 90] + [40] * 5))
HOT = PlaceForecast('San Miguel', 30.3, 32.1, 40.0, 8.25, hours([5] * 24))
MILD = PlaceForecast('Home', 24.0, 27.0, 27.5, 4.0, hours([5] * 24))


def test_fetch_reads_open_meteo_multi_place_answers():
    def item(chance):
        return {'current': {'temperature_2m': 30.5},
                'daily': {'temperature_2m_max': [31.6], 'apparent_temperature_max': [37.0], 'uv_index_max': [8.1]},
                'hourly': {'time': [f'2026-10-13T{h:02d}:00' for h in range(24)],
                           'precipitation_probability': [chance] * 24}}
    seen = {}

    def opener(url, timeout):
        seen['url'] = url
        return io.BytesIO(json.dumps([item(70), item(None)]).encode())

    result = fetch(PLACES, opener=opener)
    assert [f.name for f in result] == ['Sampaloc', 'San Miguel']
    assert result[0].rain_by_hour[14] == (14, 70) and result[1].rain_by_hour[0] == (0, 0)
    assert 'latitude=14.6088%2C15.1423' in seen['url'] and 'api.open-meteo.com' in seen['url']


def test_summary_and_advice_are_plain_and_specific():
    text = summary([RAINY, HOT], now_hour=9)
    assert 'Sampaloc: 30 degrees now, up to 32, feels like 37, rain likely from 2 PM (up to 100%).' in text
    assert 'San Miguel: 30 degrees now, up to 32, feels like 40, dry.' in text
    advice = going_out_advice([RAINY, HOT], now_hour=9)
    assert advice.startswith('Bring an umbrella: 100% chance of rain in Sampaloc from 2 PM.')
    assert 'feel like 40 degrees in San Miguel' in advice and 'sunscreen' in advice
    assert worth_asking([RAINY], 9) and not worth_asking([MILD], 9)
    assert going_out_advice([MILD], 9) == 'Looks fine out there. Have a good trip!'
    assert 'rain likely' not in summary([RAINY], now_hour=20)   # the rainy hours have passed


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def coach(tmp_path, forecasts, said, fetch_calls=None, day=datetime(2026, 10, 13, 8, 30)):
    clock = Clock()

    def fetcher(places):
        if fetch_calls is not None:
            fetch_calls.append(1)
        return forecasts

    c = WeatherCoach(PLACES, announce=said.append, state_path=tmp_path / 'weather_state.json',
                     fetcher=fetcher, clock=clock, now=lambda: day)
    return c, clock


def test_briefs_once_a_day_and_asks_about_going_out(tmp_path, monkeypatch):
    said = []
    monkeypatch.setattr('integrations.weather.threading.Thread',
                        lambda target, **_kw: type('T', (), {'start': lambda self: target()})())
    c, _clock = coach(tmp_path, [RAINY, HOT], said)
    c.maybe_brief(False)
    assert said == []                                    # nobody at the desk yet
    c.maybe_brief(True)
    assert len(said) == 1 and said[0].startswith('Good morning! Weather today. Sampaloc:')
    assert said[0].endswith('Are you going out today?')
    c.maybe_brief(True)
    assert len(said) == 1                                # once per day
    again, _ = coach(tmp_path, [RAINY, HOT], said)     # a restart the same day
    again.maybe_brief(True)
    assert len(said) == 1


def test_going_out_answers_and_weather_questions(tmp_path, monkeypatch):
    said, calls = [], []
    monkeypatch.setattr('integrations.weather.threading.Thread',
                        lambda target, **_kw: type('T', (), {'start': lambda self: target()})())
    c, clock = coach(tmp_path, [RAINY, HOT], said, calls)
    c.maybe_brief(True)
    assert c.intercept('yes, later this afternoon').startswith('Bring an umbrella')
    assert c.intercept('yes') is None                    # the question was answered already
    assert c.intercept('will it rain?').startswith('Sampaloc:')
    assert 'Bring an umbrella' in c.intercept('do I need an umbrella to go out?')
    assert c.intercept('open my notes') is None
    assert len(calls) == 1                               # one forecast fetch, then the 30-minute cache


def test_saying_no_and_an_unreachable_service(tmp_path, monkeypatch):
    said = []
    monkeypatch.setattr('integrations.weather.threading.Thread',
                        lambda target, **_kw: type('T', (), {'start': lambda self: target()})())
    c, _ = coach(tmp_path, [RAINY], said)
    c.maybe_brief(True)
    assert c.intercept("no, I'm staying home") == 'Staying in, nice. Back to focus.'

    def broken(_places):
        raise OSError('offline')

    offline = WeatherCoach(PLACES, announce=said.append, state_path=tmp_path / 'other.json', fetcher=broken)
    assert 'cannot reach the weather service' in offline.intercept('weather?')


def test_settings_keep_places_validated(tmp_path):
    from config import load_config
    path = tmp_path / 'config.yaml'
    path.write_text('weather:\n  places: [{name: Sampaloc, latitude: 14.6088, longitude: 120.9961}]\n', encoding='utf-8')
    assert load_config(path).weather.places == ({'name': 'Sampaloc', 'latitude': 14.6088, 'longitude': 120.9961},)
    path.write_text('weather:\n  places: [{name: X, latitude: 200, longitude: 0}]\n', encoding='utf-8')
    with pytest.raises(ValueError):
        load_config(path)
