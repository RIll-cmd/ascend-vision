"""Calorie and water targets, a meal schedule, and a local food/water diary.

General guidance, not medical advice:
- Calories: Mifflin-St Jeor resting energy x activity factor; lose = -500 kcal/day
  (about 0.5 kg a week), gain = +300; never below 1,200 (female) / 1,500 (male).
- Water to drink: 35 ml per kg, kept between 2.2 L (female) / 3.0 L (male) as a floor and
  3.7 L as a ceiling (US National Academies 2004 total-water intakes, ~80 % from drinks),
  plus 0.5 L on hot days. One glass = 250 ml.
- Meals: first within about an hour of waking, last at least 3 hours before bed, spaced
  roughly 3-5 hours; a snack is added when a gap would be longer than 5 hours.
Everything is stored only on this computer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
import sqlite3

ACTIVITY = {
    'sedentary': (1.2, 'little or no exercise'),
    'light': (1.375, 'light exercise 1-3 days a week'),
    'moderate': (1.55, 'moderate exercise 3-5 days a week'),
    'active': (1.725, 'hard exercise 6-7 days a week'),
    'very active': (1.9, 'physical job or training twice a day'),
}
GOALS = {'lose': -500, 'keep': 0, 'gain': 300}
GLASS_ML = 250


@dataclass(frozen=True)
class Profile:
    sex: str                 # 'male' | 'female'
    age: int
    height_cm: float
    weight_kg: float
    activity: str            # key of ACTIVITY
    goal: str                # key of GOALS
    wake: str                # 'HH:MM'
    bed: str                 # 'HH:MM'
    calorie_override: int | None = None

    def __post_init__(self):
        if self.sex not in {'male', 'female'} or self.activity not in ACTIVITY or self.goal not in GOALS:
            raise ValueError('profile choices are invalid')
        if not (13 <= self.age <= 100 and 120 <= self.height_cm <= 230 and 30 <= self.weight_kg <= 300):
            raise ValueError('profile numbers are out of range')
        for value in (self.wake, self.bed):
            datetime.strptime(value, '%H:%M')
        if self.calorie_override is not None and not 1000 <= self.calorie_override <= 6000:
            raise ValueError('calorie target must be between 1000 and 6000')


def bmr(p: Profile) -> float:
    base = 10 * p.weight_kg + 6.25 * p.height_cm - 5 * p.age
    return base + (5 if p.sex == 'male' else -161)


def tdee(p: Profile) -> float:
    return bmr(p) * ACTIVITY[p.activity][0]


def calorie_floor(p: Profile) -> int:
    return 1500 if p.sex == 'male' else 1200


def calorie_target(p: Profile) -> int:
    if p.calorie_override:
        return p.calorie_override
    return int(round(max(calorie_floor(p), tdee(p) + GOALS[p.goal]) / 10) * 10)


def calorie_explanation(p: Profile) -> str:
    if p.calorie_override:
        return f'You set {p.calorie_override:,} kcal a day yourself.'
    factor, words = ACTIVITY[p.activity]
    change = GOALS[p.goal]
    step = {'lose': ' minus 500 to lose about 0.5 kg a week', 'keep': ' to keep your weight',
            'gain': ' plus 300 to gain slowly'}[p.goal]
    raw = tdee(p) + change
    floor = (f' That is below the safe floor, so I use {calorie_floor(p):,}.'
             if raw < calorie_floor(p) else '')
    return (f'Your body uses about {bmr(p):,.0f} kcal at rest; with {words} (x{factor}) that is about '
            f'{tdee(p):,.0f} a day,{step}.{floor}')


def bmi(p: Profile) -> float:
    return p.weight_kg / (p.height_cm / 100) ** 2


def water_target_ml(p: Profile, *, hot_day: bool = False) -> int:
    floor = 3000 if p.sex == 'male' else 2200
    target = min(3700, max(floor, 35 * p.weight_kg)) + (500 if hot_day else 0)
    return int(math.ceil(target / GLASS_ML) * GLASS_ML)


def _at(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, datetime.strptime(hhmm, '%H:%M').time())


def meal_plan(p: Profile, day: date) -> list[tuple[str, datetime]]:
    """Named meal times for one day, from the owner's wake and bed times."""
    wake = _at(day, p.wake)
    bed = _at(day, p.bed)
    if bed <= wake:
        bed += timedelta(days=1)                 # sleeps after midnight
    first = wake + timedelta(hours=1)
    last = bed - timedelta(hours=3)
    if last - first < timedelta(hours=4):        # very short day: two meals
        return [('first meal', first), ('last meal', max(first + timedelta(hours=3), last))]
    middle = first + (last - first) / 2
    plan = [('breakfast', first), ('lunch', middle), ('dinner', last)]
    for (_name, start), (_next, end) in zip(plan[:-1], plan[1:]):
        if end - start > timedelta(hours=5):
            plan.append(('snack', start + (end - start) / 2))
    return sorted(plan, key=lambda item: item[1])


class NutritionStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                'CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);'
                'CREATE TABLE IF NOT EXISTS food (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, '
                '  day TEXT NOT NULL, description TEXT NOT NULL, kcal INTEGER NOT NULL, source TEXT NOT NULL);'
                'CREATE TABLE IF NOT EXISTS water (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, '
                '  day TEXT NOT NULL, ml INTEGER NOT NULL);')

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=2)
        connection.row_factory = sqlite3.Row
        return connection

    def profile(self) -> Profile | None:
        with self._connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key = 'profile'").fetchone()
        if row is None:
            return None
        try:
            return Profile(**json.loads(row['value']))
        except (TypeError, ValueError):
            return None

    def save_profile(self, profile: Profile) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO settings VALUES ('profile', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                       (json.dumps(asdict(profile)),))

    def flag(self, key: str) -> bool:
        with self._connect() as db:
            return db.execute('SELECT 1 FROM settings WHERE key = ?', (key,)).fetchone() is not None

    def set_flag(self, key: str) -> None:
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO settings VALUES (?, '1')", (key,))

    def add_food(self, description: str, kcal: int, source: str, at: datetime) -> int:
        with self._connect() as db:
            cursor = db.execute('INSERT INTO food (at, day, description, kcal, source) VALUES (?, ?, ?, ?, ?)',
                                (at.isoformat(timespec='seconds'), at.date().isoformat(), description[:200],
                                 int(kcal), source))
            return int(cursor.lastrowid)

    def update_food(self, entry_id: int, kcal: int) -> bool:
        with self._connect() as db:
            return db.execute("UPDATE food SET kcal = ?, source = 'owner' WHERE id = ?",
                              (int(kcal), entry_id)).rowcount == 1

    def delete_food(self, entry_id: int) -> bool:
        with self._connect() as db:
            return db.execute('DELETE FROM food WHERE id = ?', (entry_id,)).rowcount == 1

    def foods(self, day: date) -> list[dict]:
        with self._connect() as db:
            rows = db.execute('SELECT id, at, description, kcal, source FROM food WHERE day = ? ORDER BY at',
                              (day.isoformat(),)).fetchall()
        return [dict(row) for row in rows]

    def last_food_at(self) -> datetime | None:
        with self._connect() as db:
            row = db.execute('SELECT at FROM food ORDER BY at DESC LIMIT 1').fetchone()
        return datetime.fromisoformat(row['at']) if row else None

    def add_water(self, ml: int, at: datetime) -> None:
        with self._connect() as db:
            db.execute('INSERT INTO water (at, day, ml) VALUES (?, ?, ?)',
                       (at.isoformat(timespec='seconds'), at.date().isoformat(), int(ml)))

    def water_ml(self, day: date) -> int:
        with self._connect() as db:
            row = db.execute('SELECT COALESCE(SUM(ml), 0) AS total FROM water WHERE day = ?', (day.isoformat(),)).fetchone()
        return int(row['total'])
