"""Today's weather for the owner's places from Open-Meteo (free, no key), turned into plain advice.

Everything here is fixed wording: no AI is used. The forecast is fetched at most every
30 minutes and only the place coordinates leave this computer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import logging
from pathlib import Path
import re
import threading
import time
import urllib.parse
import urllib.request

LOG = logging.getLogger(__name__)
API = 'https://api.open-meteo.com/v1/forecast'
RAIN_LIKELY = 50
RAIN_POSSIBLE = 30
UMBRELLA = 40
HOT_FEELS = 35.0
HIGH_UV = 8.0

_WEATHER_QUESTION = re.compile(r'\b(weather|forecast|rain|raining|rainy|umbrella|ulan|panahon)\b', re.I)
_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|i am|i'?ll|will|going|oo|opo)\b", re.I)
_NO = re.compile(r"\b(no|nope|nah|not|staying|stay in|home|hindi)\b", re.I)


@dataclass(frozen=True)
class Place:
    name: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class PlaceForecast:
    name: str
    now_temp: float
    max_temp: float
    feels_max: float
    uv_max: float
    rain_by_hour: tuple[tuple[int, int], ...]   # (hour 0-23, chance %)


def _hour_label(hour: int) -> str:
    suffix = 'AM' if hour < 12 else 'PM'
    return f'{hour % 12 or 12} {suffix}'


def fetch(places: list[Place], *, opener=urllib.request.urlopen, timeout: float = 10.0) -> list[PlaceForecast]:
    query = urllib.parse.urlencode({
        'latitude': ','.join(f'{p.latitude:.4f}' for p in places),
        'longitude': ','.join(f'{p.longitude:.4f}' for p in places),
        'hourly': 'precipitation_probability',
        'daily': 'temperature_2m_max,apparent_temperature_max,uv_index_max',
        'current': 'temperature_2m',
        'timezone': 'auto', 'forecast_days': 1,
    })
    with opener(f'{API}?{query}', timeout=timeout) as response:
        payload = json.loads(response.read().decode('utf-8'))
    items = payload if isinstance(payload, list) else [payload]
    if len(items) != len(places):
        raise ValueError('forecast does not match the requested places')
    result = []
    for place, item in zip(places, items):
        hourly, daily = item['hourly'], item['daily']
        hours = tuple((int(stamp[11:13]), int(chance or 0))
                      for stamp, chance in zip(hourly['time'], hourly['precipitation_probability']))
        result.append(PlaceForecast(
            place.name, float(item['current']['temperature_2m']), float(daily['temperature_2m_max'][0]),
            float(daily['apparent_temperature_max'][0]), float(daily['uv_index_max'][0] or 0), hours))
    return result


def _rain(forecast: PlaceForecast, from_hour: int) -> tuple[int, int | None]:
    """Highest chance for the rest of the day, and the first hour it is likely."""
    later = [(hour, chance) for hour, chance in forecast.rain_by_hour if from_hour <= hour <= 22]
    if not later:
        return 0, None
    peak = max(chance for _hour, chance in later)
    start = next((hour for hour, chance in later if chance >= RAIN_LIKELY), None)
    return peak, start


def summary(forecasts: list[PlaceForecast], now_hour: int) -> str:
    parts = []
    for item in forecasts:
        peak, start = _rain(item, now_hour)
        if start is not None:
            rain = f'rain likely from {_hour_label(start)} (up to {peak}%)'
        elif peak >= RAIN_POSSIBLE:
            rain = f'a small chance of rain ({peak}%)'
        else:
            rain = 'dry'
        feels = f', feels like {item.feels_max:.0f}' if item.feels_max - item.max_temp >= 2 else ''
        parts.append(f'{item.name}: {item.now_temp:.0f} degrees now, up to {item.max_temp:.0f}{feels}, {rain}.')
    return ' '.join(parts)


def worth_asking(forecasts: list[PlaceForecast], now_hour: int) -> bool:
    return any(_rain(item, now_hour)[0] >= UMBRELLA or item.feels_max >= HOT_FEELS or item.uv_max >= HIGH_UV
               for item in forecasts)


def going_out_advice(forecasts: list[PlaceForecast], now_hour: int) -> str:
    tips = []
    wet = [(item.name, *_rain(item, now_hour)) for item in forecasts]
    wet = [(name, peak, start) for name, peak, start in wet if peak >= UMBRELLA]
    if wet:
        name, peak, start = max(wet, key=lambda row: row[1])
        when = f' from {_hour_label(start)}' if start is not None else ''
        tips.append(f'Bring an umbrella: {peak}% chance of rain in {name}{when}.')
    hottest = max(forecasts, key=lambda item: item.feels_max)
    if hottest.feels_max >= HOT_FEELS:
        tips.append(f'It will feel like {hottest.feels_max:.0f} degrees in {hottest.name}, so bring water and wear something light.')
    if max(item.uv_max for item in forecasts) >= HIGH_UV:
        tips.append('UV is very high around midday; use sunscreen or a cap.')
    return ' '.join(tips) if tips else 'Looks fine out there. Have a good trip!'


class WeatherCoach:
    """Morning briefing once a day, the 'going out?' follow-up, and answers to weather questions."""

    def __init__(self, places: list[Place], *, announce, state_path: Path, fetcher=fetch,
                 clock=time.monotonic, now=datetime.now, ask_window: float = 1800.0, cache_seconds: float = 1800.0):
        self._places, self._announce, self._fetch = list(places), announce, fetcher
        self._state_path, self._clock, self._now = Path(state_path), clock, now
        self._ask_window, self._cache_seconds = ask_window, cache_seconds
        self._lock = threading.Lock()
        self._cache: tuple[float, list[PlaceForecast]] | None = None
        self._asked_at: float | None = None
        self._briefing = False
        try:
            self._briefed_date = json.loads(self._state_path.read_text(encoding='utf-8')).get('briefed')
        except (OSError, ValueError):
            self._briefed_date = None

    def forecasts(self) -> list[PlaceForecast]:
        with self._lock:
            cached = self._cache
        if cached and self._clock() - cached[0] < self._cache_seconds:
            return cached[1]
        result = self._fetch(self._places)
        with self._lock:
            self._cache = (self._clock(), result)
        return result

    def hot_today(self) -> bool:
        """From the cached forecast only, so it never waits on the network."""
        with self._lock:
            cached = self._cache
        return bool(cached) and any(item.feels_max >= HOT_FEELS for item in cached[1])

    def _briefed_today(self) -> bool:
        return self._briefed_date == self._now().date().isoformat()

    def _mark_briefed(self) -> None:
        self._briefed_date = self._now().date().isoformat()
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps({'briefed': self._briefed_date}), encoding='utf-8')
        except OSError as exc:
            LOG.info('Weather briefing date not saved (%s)', type(exc).__name__)

    def maybe_brief(self, owner_present: bool) -> None:
        """Called often from the camera loop; starts at most one background briefing per day."""
        if not owner_present or self._briefing or not self._places or self._briefed_today():
            return
        self._briefing = True
        threading.Thread(target=self._brief, name='vision-weather-briefing', daemon=True).start()

    def _brief(self) -> None:
        try:
            forecasts = self.forecasts()
            hour = self._now().hour
            greeting = 'Good morning! ' if hour < 12 else ''
            text = f'{greeting}Weather today. {summary(forecasts, hour)}'
            if worth_asking(forecasts, hour):
                text += ' Are you going out today?'
                with self._lock:
                    self._asked_at = self._clock()
            self._mark_briefed()
            self._announce(text)
        except Exception as exc:
            LOG.info('Weather briefing skipped (%s)', type(exc).__name__)
        finally:
            self._briefing = False

    def intercept(self, text: str) -> str | None:
        """Answer the 'going out?' follow-up and direct weather questions without AI."""
        words = text.strip()
        with self._lock:
            pending = self._asked_at is not None and self._clock() - self._asked_at < self._ask_window
        if pending and (_YES.search(words) or _NO.search(words)) and not _WEATHER_QUESTION.search(words):
            with self._lock:
                self._asked_at = None
            if _NO.search(words) and not re.search(r"\b(yes|yeah|yep|oo|opo)\b", words, re.I):
                return 'Staying in, nice. Back to focus.'
            try:
                return going_out_advice(self.forecasts(), self._now().hour)
            except Exception:
                return 'Have a good trip! I could not check the weather just now, so maybe bring an umbrella.'
        if _WEATHER_QUESTION.search(words) and self._places:
            try:
                forecasts, hour = self.forecasts(), self._now().hour
            except Exception as exc:
                LOG.info('Weather unavailable (%s)', type(exc).__name__)
                return 'I cannot reach the weather service right now. Try again in a bit.'
            answer = summary(forecasts, hour)
            if re.search(r'\b(umbrella|go out|going out|outside|labas)\b', words, re.I):
                answer += ' ' + going_out_advice(forecasts, hour)
            return answer
        return None
