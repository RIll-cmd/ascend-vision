"""Food and water by chat or voice: a short setup Q&A, logging, quick answers and reminders.

Only the food description is ever sent to an AI (to estimate calories); the owner can
correct any number. Everything else here is fixed wording.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import logging
import re
import threading
from typing import Callable

from assistant.nutrition import (ACTIVITY, GLASS_ML, NutritionStore, Profile, bmi, calorie_explanation,
                                 calorie_target, meal_plan, water_target_ml)

LOG = logging.getLogger(__name__)
SETUP = re.compile(r'\b(set ?up|start|create|make|redo|update)\b.*\b(food|meal|calorie|diet|nutrition|water)\b.*\bplan\b'
                   r'|\b(food|meal|calorie|nutrition) plan\b', re.I)
CANCEL = re.compile(r'^\s*(cancel|stop|never ?mind|skip)\b', re.I)
SET_TARGET = re.compile(r'\b(?:calorie|kcal)\s*(?:target|goal|limit)?\s*(?:to|is|of|=)?\s*(\d{3,4})\b'
                        r'|\b(?:target|goal)\s*(?:to|is|of)?\s*(\d{3,4})\s*(?:k?cal|calories)\b', re.I)
WATER = re.compile(r'\b(drank|drink|drinking|had|finished)\b.*\b(water|glass(?:es)?|bottle|tubig|ml|liters?|litres?|l)\b'
                   r'|\b(water|tubig)\b.*\b(glass|bottle|ml|liters?|litres?)\b', re.I)
FOOD = re.compile(r"\b(i\s+(?:just\s+)?(?:ate|had|eaten|finished)|ate\s|kumain|for (?:breakfast|lunch|dinner|merienda|snack)"
                  r"|log (?:food|meal))\b", re.I)
CORRECT = re.compile(r'\b(?:that was|it was|make it|change (?:it|that) to|actually)\s*(?:about\s*)?(\d{2,4})\s*(?:k?cal|calories)?\b', re.I)
DELETE = re.compile(r'\b(delete|remove|undo)\b.*\b(last|that)\b.*\b(food|meal|entry|log)?', re.I)
LEFT = re.compile(r'\b(calories?|kcal)\b.*\b(left|remaining|today|so far)\b|\bhow (?:much|many) (?:can i|left)\b', re.I)
WATER_Q = re.compile(r'\b(how much|how many)\b.*\b(water|glasses)\b|\bwater (?:today|so far)\b', re.I)
EATING_WORDS = re.compile(r'\b(ate|eat|eaten|kumain|breakfast|lunch|dinner|snack|merienda|rice|chicken)\b', re.I)
ATE_Q = re.compile(r'\bwhat (?:did|have) i (?:eat|eaten|had)\b', re.I)

QUESTIONS = [
    ('wake', 'What time do you usually wake up?'),
    ('bed', 'And what time do you usually go to sleep?'),
    ('sex', 'For the calorie formula: are you male or female?'),
    ('age', 'How old are you?'),
    ('height', "How tall are you? Centimeters, or feet and inches like 5'7."),
    ('weight', 'How much do you weigh? Kilograms or pounds.'),
    ('activity', 'How active are you: sedentary, light, moderate, active, or very active?'),
    ('goal', 'Last one: do you want to lose weight, keep it, or gain?'),
]


def parse_time(text: str, *, evening: bool = False) -> str | None:
    match = re.search(r'\b(\d{1,2})(?::|\.)?(\d{2})?\s*(am|pm|a\.m\.|p\.m\.)?\b', text, re.I)
    if not match:
        return None
    hour, minute, half = int(match.group(1)), int(match.group(2) or 0), (match.group(3) or '').lower()
    if minute > 59 or hour > 23:
        return None
    if half.startswith('p') and hour < 12:
        hour += 12
    if half.startswith('a') and hour == 12:
        hour = 0
    if not half and evening and 5 <= hour <= 11:
        hour += 12                                  # "11" for a bedtime means 11 PM
    return f'{hour:02d}:{minute:02d}'


def parse_height_cm(text: str) -> float | None:
    feet = re.search(r"(\d)\s*(?:'|ft|feet|foot)\s*(\d{1,2})?\s*(?:\"|in|inches)?", text, re.I)
    if feet:
        return round(int(feet.group(1)) * 30.48 + int(feet.group(2) or 0) * 2.54, 1)
    meters = re.search(r'\b([12][.,]\d{1,2})\s*m\b', text, re.I)
    if meters:
        return float(meters.group(1).replace(',', '.')) * 100
    cm = re.search(r'\b(\d{3})(?:\s*cm)?\b', text, re.I)
    return float(cm.group(1)) if cm else None


def parse_weight_kg(text: str) -> float | None:
    match = re.search(r'\b(\d{2,3}(?:[.,]\d)?)\s*(kg|kgs|kilos?|lbs?|pounds?)?\b', text, re.I)
    if not match:
        return None
    value = float(match.group(1).replace(',', '.'))
    unit = (match.group(2) or 'kg').lower()
    return round(value * 0.4536, 1) if unit.startswith(('lb', 'pound')) else value


def parse_choice(field: str, text: str) -> str | None:
    words = text.lower()
    if field == 'sex':
        if re.search(r'\b(female|woman|girl|f)\b', words):
            return 'female'
        if re.search(r'\b(male|man|boy|m)\b', words):
            return 'male'
        return None
    if field == 'activity':
        for key in ('very active', 'sedentary', 'moderate', 'active', 'light'):
            if key in words:
                return key
        number = re.search(r'\b([1-5])\b', words)
        return list(ACTIVITY)[int(number.group(1)) - 1] if number else None
    if field == 'goal':
        if re.search(r'\b(lose|cut|slim|loss)\b', words):
            return 'lose'
        if re.search(r'\b(gain|bulk|build)\b', words):
            return 'gain'
        if re.search(r'\b(keep|maintain|same|stay)\b', words):
            return 'keep'
    return None


def water_ml_from(text: str) -> int:
    ml = re.search(r'\b(\d{2,4})\s*ml\b', text, re.I)
    if ml:
        return int(ml.group(1))
    liters = re.search(r'\b(\d(?:[.,]\d+)?)\s*(?:l|liters?|litres?)\b', text, re.I)
    if liters:
        return int(float(liters.group(1).replace(',', '.')) * 1000)
    count = re.search(r'\b(\d|two|three|four)\s+(glass|glasses|bottles?)\b', text, re.I)
    words = {'two': 2, 'three': 3, 'four': 4}
    amount = 500 if re.search(r'\bbottles?\b', text, re.I) else GLASS_ML
    if count:
        raw = count.group(1).lower()
        return amount * (words.get(raw) or int(raw))
    return amount


def _kcal(value: int) -> str:
    return f'{value:,}'


class NutritionCoach:
    def __init__(self, store: NutritionStore, *, announce: Callable[[str], None],
                 estimate: Callable[[str], list[tuple[str, int]]] | None = None,
                 hot_day: Callable[[], bool] = lambda: False, now=datetime.now):
        self._store, self._announce, self._estimate = store, announce, estimate
        self.hot_day, self._now = hot_day, now
        self._lock = threading.Lock()
        self._answers: dict | None = None          # setup Q&A in progress
        self._pending_food: str | None = None      # waiting for a calorie number
        self._last_entry: int | None = None
        self._reminded: set[tuple[str, str]] = set()
        self._last_water_nudge: datetime | None = None
        self._last_hunger_nudge: datetime | None = None

    # --- summaries ----------------------------------------------------------------
    def _today_numbers(self):
        today = self._now().date()
        eaten = sum(item['kcal'] for item in self._store.foods(today))
        return today, eaten, self._store.water_ml(today)

    def _left_line(self, profile: Profile | None) -> str:
        _today, eaten, _water = self._today_numbers()
        if profile is None:
            return f"{_kcal(eaten)} kcal so far today. Say 'set up my food plan' for a daily target."
        target = calorie_target(profile)
        left = target - eaten
        if left >= 0:
            return f'{_kcal(eaten)} of {_kcal(target)} kcal today, {_kcal(left)} left.'
        return f'{_kcal(eaten)} of {_kcal(target)} kcal today, {_kcal(-left)} over.'

    def _water_line(self, profile: Profile | None) -> str:
        _today, _eaten, water = self._today_numbers()
        if profile is None:
            return f'{water / 1000:.2g} L of water so far today.'
        target = water_target_ml(profile, hot_day=self.hot_day())
        return (f'{water / 1000:.2g} of {target / 1000:.2g} L of water today '
                f'({water // GLASS_ML} of {target // GLASS_ML} glasses).')

    def plan_summary(self, profile: Profile) -> str:
        meals = ', '.join(f"{name} around {when.strftime('%I:%M %p').lstrip('0')}"
                          for name, when in meal_plan(profile, self._now().date()))
        target = water_target_ml(profile)
        return (f'Your plan: about {_kcal(calorie_target(profile))} kcal a day. {calorie_explanation(profile)} '
                f'BMI {bmi(profile):.1f}. Water: about {target / 1000:.2g} L a day ({target // GLASS_ML} glasses), '
                f'more on hot days. Meals: {meals}. You can say "change my calorie target to 2000" anytime. '
                f'This is general guidance, not medical advice.')

    # --- chat --------------------------------------------------------------------
    def intercept(self, text: str) -> str | None:
        words = text.strip()
        with self._lock:
            if self._answers is not None:
                return self._setup_step(words)
            if self._pending_food is not None:
                number = re.search(r'\b(\d{2,4})\b', words)
                description, self._pending_food = self._pending_food, None
                if number:
                    self._last_entry = self._store.add_food(description, int(number.group(1)), 'owner', self._now())
                    return f'Logged {description}: {_kcal(int(number.group(1)))} kcal. {self._left_line(self._store.profile())}'
        if SETUP.search(words):
            with self._lock:
                self._answers = {}
            return "Sure, 8 quick questions. Say 'cancel' anytime. " + QUESTIONS[0][1]
        target = SET_TARGET.search(words)
        if target:
            return self._set_target(int(target.group(1) or target.group(2)))
        if CORRECT.search(words) and self._last_entry is not None:
            kcal = int(CORRECT.search(words).group(1))
            if self._store.update_food(self._last_entry, kcal):
                return f'Fixed: {_kcal(kcal)} kcal. {self._left_line(self._store.profile())}'
        if DELETE.search(words) and self._last_entry is not None:
            if self._store.delete_food(self._last_entry):
                self._last_entry = None
                return f'Removed it. {self._left_line(self._store.profile())}'
        profile = self._store.profile()
        if WATER.search(words) and not EATING_WORDS.search(words):
            ml = water_ml_from(words)
            self._store.add_water(ml, self._now())
            return f'Logged {ml} ml of water. {self._water_line(profile)}'
        if FOOD.search(words):
            return self._log_food(words, profile)
        if WATER_Q.search(words):
            return self._water_line(profile)
        if LEFT.search(words):
            return self._left_line(profile)
        if ATE_Q.search(words):
            items = self._store.foods(self._now().date())
            if not items:
                return 'Nothing logged yet today.'
            listed = '; '.join(f"{item['description']} ({_kcal(item['kcal'])} kcal)" for item in items)
            return f'Today: {listed}. {self._left_line(profile)}'
        return None

    def _log_food(self, words: str, profile: Profile | None) -> str:
        description = re.sub(FOOD, '', words).strip(' .,!') or words
        try:
            items = self._estimate(description) if self._estimate else None
        except Exception as exc:
            LOG.info('Calorie estimate unavailable (%s)', type(exc).__name__)
            items = None
        if not items:
            with self._lock:
                self._pending_food = description[:200]
            return f'I could not estimate {description}. About how many calories was it?'
        total = sum(kcal for _name, kcal in items)
        names = ', '.join(name for name, _kcal_value in items)
        self._last_entry = self._store.add_food(names or description, total, 'ai', self._now())
        return (f'Logged {names}: about {_kcal(total)} kcal (estimate). {self._left_line(profile)} '
                f"Say 'that was 500' to fix the number.")

    def _set_target(self, kcal: int) -> str:
        profile = self._store.profile()
        if profile is None:
            return "Let's set up your plan first so I know your meal times. Say 'set up my food plan'."
        try:
            self._store.save_profile(replace(profile, calorie_override=kcal))
        except ValueError:
            return 'Please pick a target between 1,000 and 6,000 kcal.'
        return f'Done: {_kcal(kcal)} kcal a day. {self._left_line(self._store.profile())}'

    def _setup_step(self, words: str) -> str:
        """Lock held. Record one answer and ask the next question."""
        if CANCEL.match(words):
            self._answers = None
            return "Okay, setup cancelled. Say 'set up my food plan' whenever you like."
        field, question = QUESTIONS[len(self._answers)]
        parsers = {'wake': parse_time, 'bed': lambda t: parse_time(t, evening=True), 'height': parse_height_cm, 'weight': parse_weight_kg,
                   'age': lambda t: int(m.group(1)) if (m := re.search(r'\b(\d{1,3})\b', t)) else None}
        value = parsers[field](words) if field in parsers else parse_choice(field, words)
        if value is None:
            return f"Sorry, I didn't catch that. {question}"
        self._answers[field] = value
        if len(self._answers) < len(QUESTIONS):
            return QUESTIONS[len(self._answers)][1]
        answers, self._answers = self._answers, None
        try:
            profile = Profile(sex=answers['sex'], age=answers['age'], height_cm=answers['height'],
                              weight_kg=answers['weight'], activity=answers['activity'], goal=answers['goal'],
                              wake=answers['wake'], bed=answers['bed'])
        except ValueError:
            return "Some of those numbers look off, so I didn't save them. Say 'set up my food plan' to try again."
        self._store.save_profile(profile)
        return self.plan_summary(profile)

    # --- reminders ---------------------------------------------------------------
    def maybe_remind(self, owner_available: bool) -> None:
        """Cheap; call every few seconds from the main loop."""
        if not owner_available:
            return
        now = self._now()
        profile = self._store.profile()
        if profile is None:
            if not self._store.flag('offered_setup'):
                self._store.set_flag('offered_setup')
                self._announce("I can remind you to eat and drink water. Say 'set up my food plan' and "
                               "I'll ask a few quick questions.")
            return
        day = now.date()
        wake = datetime.combine(day, datetime.strptime(profile.wake, '%H:%M').time())
        bed_time = datetime.combine(day, datetime.strptime(profile.bed, '%H:%M').time())
        if bed_time <= wake:
            bed_time += timedelta(days=1)
        if not wake <= now <= bed_time - timedelta(minutes=30):
            return                                   # outside waking hours
        last_food = self._store.last_food_at()
        for name, when in meal_plan(profile, day):
            key = (day.isoformat(), name)
            if when <= now <= when + timedelta(minutes=90) and key not in self._reminded:
                self._reminded.add(key)
                if last_food is None or last_food < when - timedelta(minutes=90):
                    self._announce(f"It's about {name} time. Maybe take a break and eat? {self._left_line(profile)}")
                    return
        if last_food is not None and now - last_food > timedelta(hours=5) and (
                self._last_hunger_nudge is None or now - self._last_hunger_nudge > timedelta(hours=2)):
            self._last_hunger_nudge = now
            self._announce("It's been over 5 hours since you last ate. Maybe take a break and eat?")
            return
        target = water_target_ml(profile, hot_day=self.hot_day())
        awake = (bed_time - wake).total_seconds()
        expected = target * min(1.0, (now - wake).total_seconds() / awake)
        drunk = self._store.water_ml(day)
        interval = timedelta(seconds=awake / max(1, target // GLASS_ML))
        if expected - drunk >= GLASS_ML and (
                self._last_water_nudge is None or now - self._last_water_nudge >= interval):
            self._last_water_nudge = now
            heat = " It's a hot day, so drink a bit more." if self.hot_day() else ''
            self._announce(f"Water break? You're at {drunk // GLASS_ML} of {target // GLASS_ML} glasses today.{heat}")


ESTIMATE_PROMPT = ('You estimate food calories. Reply with JSON only: {"items": [{"name": "short food name", '
                   '"kcal": integer}]}. Use typical home portions in the Philippines when no amount is given. '
                   'One item per food; at most 10 items.')


def make_estimator(router, providers=('groq', 'gemini', 'cerebras')):
    """Ask one configured AI provider for a calorie estimate; only the food description is sent."""
    def estimate(description: str) -> list[tuple[str, int]]:
        last_error = None
        for provider in providers:
            pool = getattr(router, f'_{provider}_pool', None)
            if pool is None or not pool.has_keys:
                continue
            try:
                answer = router.generate_browser_structured_response(
                    provider, description[:300], system_prompt=ESTIMATE_PROMPT, max_tokens=200)
            except Exception as exc:
                last_error = exc
                continue
            items = answer.data.get('items') if isinstance(answer.data, dict) else None
            if not isinstance(items, list) or not 1 <= len(items) <= 10:
                raise ValueError('calorie estimate has no items')
            parsed = []
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError('calorie estimate is malformed')
                name, kcal = item.get('name'), item.get('kcal')
                if not isinstance(name, str) or not name.strip() or type(kcal) is not int or not 0 <= kcal <= 3000:
                    raise ValueError('calorie estimate is malformed')
                parsed.append((name.strip()[:60], kcal))
            return parsed
        raise RuntimeError('no AI provider could estimate calories') from last_error
    return estimate
