"""Opt-in, local daily aggregates; no raw observation timeline is persisted."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import math
from pathlib import Path
import sqlite3
import threading
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


RETENTION_DAYS = 30
RULE_VERSION = "daily-activity-v1"
MAX_SAMPLE_GAP_SECONDS = 5.0
MAX_CLOCK_DRIFT_SECONDS = 2.0
_METRICS = {
    "focus_session_seconds": "focus_session_seconds",
    "declared_break_seconds": "declared_break_seconds",
    "entertainment_category_seconds": "entertainment_category_seconds",
}


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _day(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("date must use YYYY-MM-DD") from exc
    raise ValueError("date must use YYYY-MM-DD")


def _timezone(value: str) -> ZoneInfo | None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timezone must be an IANA timezone name")
    if value == "local":
        return None
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("timezone must be an available IANA timezone") from exc


@dataclass(frozen=True)
class ActivityCorrection:
    correction_id: int
    metric: str
    removed_seconds: int
    created_at: str


@dataclass(frozen=True)
class DailySummary:
    local_date: str
    timezone: str
    tracked_seconds: int
    focus_session_seconds: int
    focus_coverage_seconds: int
    declared_break_seconds: int
    break_coverage_seconds: int
    entertainment_category_seconds: int
    entertainment_coverage_seconds: int
    rule_version: str
    corrections: tuple[ActivityCorrection, ...] = ()

    @property
    def focus_uncovered_seconds(self) -> int:
        return max(0, self.tracked_seconds - self.focus_coverage_seconds)

    @property
    def break_uncovered_seconds(self) -> int:
        return max(0, self.tracked_seconds - self.break_coverage_seconds)

    @property
    def entertainment_uncovered_seconds(self) -> int:
        return max(0, self.tracked_seconds - self.entertainment_coverage_seconds)

    def as_dict(self) -> dict:
        return {
            "date": self.local_date,
            "timezone": self.timezone,
            "tracked_seconds": self.tracked_seconds,
            "focus_session_seconds": self.focus_session_seconds,
            "focus_coverage_seconds": self.focus_coverage_seconds,
            "focus_uncovered_seconds": self.focus_uncovered_seconds,
            "declared_break_seconds": self.declared_break_seconds,
            "break_coverage_seconds": self.break_coverage_seconds,
            "break_uncovered_seconds": self.break_uncovered_seconds,
            "entertainment_category_seconds": self.entertainment_category_seconds,
            "entertainment_coverage_seconds": self.entertainment_coverage_seconds,
            "entertainment_uncovered_seconds": self.entertainment_uncovered_seconds,
            "rule_version": self.rule_version,
            "corrections": [
                {"id": item.correction_id, "metric": item.metric,
                 "removed_seconds": item.removed_seconds, "created_at": item.created_at}
                for item in self.corrections
            ],
        }


class ActivityHistoryStore:
    """Small separate SQLite store containing only opt-in day-level totals."""

    def __init__(self, path: str | Path, *, retention_days: int = RETENTION_DAYS,
                 timeout_seconds: float = 5.0, now=lambda: datetime.now(timezone.utc)):
        if isinstance(retention_days, bool) or not isinstance(retention_days, int) or retention_days != RETENTION_DAYS:
            raise ValueError("activity history retention is fixed at 30 days")
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("timeout_seconds must be positive")
        self.path = Path(path)
        self.timeout_seconds = float(timeout_seconds)
        self._now = now
        self._lock = threading.RLock()
        self._purged_dates: dict[str, date] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1))
                );
                INSERT OR IGNORE INTO settings(id, enabled) VALUES(1, 0);
                CREATE TABLE IF NOT EXISTS daily_activity (
                    local_date TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    tracked_seconds INTEGER NOT NULL DEFAULT 0 CHECK(tracked_seconds >= 0),
                    focus_session_seconds INTEGER NOT NULL DEFAULT 0 CHECK(focus_session_seconds >= 0),
                    focus_coverage_seconds INTEGER NOT NULL DEFAULT 0 CHECK(focus_coverage_seconds >= 0),
                    declared_break_seconds INTEGER NOT NULL DEFAULT 0 CHECK(declared_break_seconds >= 0),
                    break_coverage_seconds INTEGER NOT NULL DEFAULT 0 CHECK(break_coverage_seconds >= 0),
                    entertainment_category_seconds INTEGER NOT NULL DEFAULT 0 CHECK(entertainment_category_seconds >= 0),
                    entertainment_coverage_seconds INTEGER NOT NULL DEFAULT 0 CHECK(entertainment_coverage_seconds >= 0),
                    rule_version TEXT NOT NULL,
                    PRIMARY KEY(local_date, timezone)
                );
                CREATE TABLE IF NOT EXISTS activity_corrections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    local_date TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    removed_seconds INTEGER NOT NULL CHECK(removed_seconds > 0),
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_activity_corrections_day
                    ON activity_corrections(local_date, timezone);
            """)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=self.timeout_seconds)
        db.row_factory = sqlite3.Row
        db.execute(f"PRAGMA busy_timeout={int(self.timeout_seconds * 1000)}")
        return db

    @property
    def enabled(self) -> bool:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT enabled FROM settings WHERE id=1").fetchone()
            return bool(row["enabled"])

    def set_enabled(self, enabled: bool) -> bool:
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        with self._lock, self._connect() as db:
            db.execute("UPDATE settings SET enabled=? WHERE id=1", (int(enabled),))
        return enabled

    def add_interval(self, local_date: date | str, timezone_name: str, *, tracked_seconds: int,
                     focus_session_seconds: int = 0, focus_coverage_seconds: int = 0,
                     declared_break_seconds: int = 0, break_coverage_seconds: int = 0,
                     entertainment_category_seconds: int = 0,
                     entertainment_coverage_seconds: int = 0) -> bool:
        day = _day(local_date).isoformat()
        _timezone(timezone_name)
        values = {
            "tracked_seconds": tracked_seconds,
            "focus_session_seconds": focus_session_seconds,
            "focus_coverage_seconds": focus_coverage_seconds,
            "declared_break_seconds": declared_break_seconds,
            "break_coverage_seconds": break_coverage_seconds,
            "entertainment_category_seconds": entertainment_category_seconds,
            "entertainment_coverage_seconds": entertainment_coverage_seconds,
        }
        for name, value in values.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not any(values.values()):
            return False
        self.purge_expired()
        columns = ", ".join(values)
        increments = ", ".join(f"{column}=daily_activity.{column}+excluded.{column}" for column in values)
        placeholders = ", ".join("?" for _ in range(2 + len(values) + 1))
        with self._lock, self._connect() as db:
            if not db.execute("SELECT enabled FROM settings WHERE id=1").fetchone()["enabled"]:
                return False
            db.execute(
                f"INSERT INTO daily_activity(local_date, timezone, {columns}, rule_version) "
                f"VALUES({placeholders}) ON CONFLICT(local_date, timezone) DO UPDATE SET "
                f"{increments}, rule_version=excluded.rule_version",
                (day, timezone_name, *values.values(), RULE_VERSION),
            )
        return True

    def _read_summary(self, db, row) -> DailySummary:
        corrections = db.execute(
            "SELECT id, metric, removed_seconds, created_at FROM activity_corrections "
            "WHERE local_date=? AND timezone=? ORDER BY id",
            (row["local_date"], row["timezone"]),
        ).fetchall()
        correction_totals = {name: 0 for name in _METRICS}
        for item in corrections:
            correction_totals[item["metric"]] += item["removed_seconds"]
        return DailySummary(
            local_date=row["local_date"], timezone=row["timezone"],
            tracked_seconds=row["tracked_seconds"],
            focus_session_seconds=max(0, row["focus_session_seconds"] - correction_totals["focus_session_seconds"]),
            focus_coverage_seconds=max(0, row["focus_coverage_seconds"] - correction_totals["focus_session_seconds"]),
            declared_break_seconds=max(0, row["declared_break_seconds"] - correction_totals["declared_break_seconds"]),
            break_coverage_seconds=max(0, row["break_coverage_seconds"] - correction_totals["declared_break_seconds"]),
            entertainment_category_seconds=max(0, row["entertainment_category_seconds"] - correction_totals["entertainment_category_seconds"]),
            entertainment_coverage_seconds=max(0, row["entertainment_coverage_seconds"] - correction_totals["entertainment_category_seconds"]),
            rule_version=row["rule_version"],
            corrections=tuple(ActivityCorrection(item["id"], item["metric"],
                                                 item["removed_seconds"], item["created_at"])
                              for item in corrections),
        )

    def get_summary(self, local_date: date | str, *, timezone_name: str) -> DailySummary | None:
        day = _day(local_date).isoformat()
        _timezone(timezone_name)
        self.purge_expired()
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM daily_activity WHERE local_date=? AND timezone=?",
                             (day, timezone_name)).fetchone()
            return self._read_summary(db, row) if row else None

    def list_summaries(self, start: date | str, end: date | str, *, timezone_name: str) -> list[DailySummary]:
        first, last = _day(start), _day(end)
        _timezone(timezone_name)
        if first > last:
            raise ValueError("start must be on or before end")
        self.purge_expired()
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT * FROM daily_activity WHERE timezone=? AND local_date BETWEEN ? AND ? "
                "ORDER BY local_date",
                (timezone_name, first.isoformat(), last.isoformat()),
            ).fetchall()
            return [self._read_summary(db, row) for row in rows]

    def correct(self, local_date: date | str, timezone_name: str, metric: str, delta_seconds: int) -> ActivityCorrection:
        day = _day(local_date).isoformat()
        _timezone(timezone_name)
        if metric not in _METRICS:
            raise ValueError("metric is not correctable")
        if isinstance(delta_seconds, bool) or not isinstance(delta_seconds, int) or not -86_400 <= delta_seconds < 0:
            raise ValueError("correction must remove between 1 second and 24 hours")
        with self._lock, self._connect() as db:
            row = db.execute(f"SELECT {_METRICS[metric]} FROM daily_activity WHERE local_date=? AND timezone=?",
                             (day, timezone_name)).fetchone()
            if row is None:
                raise ValueError("there is no saved activity for that day")
            prior = db.execute(
                "SELECT COALESCE(SUM(removed_seconds), 0) AS removed FROM activity_corrections "
                "WHERE local_date=? AND timezone=? AND metric=?", (day, timezone_name, metric),
            ).fetchone()["removed"]
            if prior - delta_seconds > row[metric]:
                raise ValueError("correction cannot remove more time than the recorded metric")
            created_at = _aware(self._now(), "now").isoformat()
            cursor = db.execute(
                "INSERT INTO activity_corrections(local_date, timezone, metric, removed_seconds, created_at) "
                "VALUES(?, ?, ?, ?, ?)", (day, timezone_name, metric, -delta_seconds, created_at),
            )
            return ActivityCorrection(cursor.lastrowid, metric, -delta_seconds, created_at)

    def export(self, start: date | str | None = None, *, timezone_name: str,
               now: datetime | None = None) -> list[dict]:
        current = _aware(now or self._now(), "now")
        last = current.astimezone(_timezone(timezone_name)).date()
        first = last - timedelta(days=RETENTION_DAYS - 1)
        rows = self.list_summaries(start or first, last, timezone_name=timezone_name)
        return [summary.as_dict() for summary in rows]

    def purge_expired(self, *, now: datetime | date | None = None) -> int:
        current = now or self._now()
        if isinstance(current, date) and not isinstance(current, datetime):
            current = datetime.combine(current, time.min, timezone.utc)
        current_utc = _aware(current, "now")
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            zones = db.execute(
                "SELECT timezone FROM daily_activity UNION SELECT timezone FROM activity_corrections"
            ).fetchall()
            deleted = 0
            for row in zones:
                timezone_name = row["timezone"]
                zone = _timezone(timezone_name)
                local_today = current_utc.astimezone(zone).date()
                if self._purged_dates.get(timezone_name) == local_today:
                    continue
                cutoff = (local_today - timedelta(days=RETENTION_DAYS - 1)).isoformat()
                deleted += db.execute(
                    "DELETE FROM daily_activity WHERE timezone=? AND local_date < ?",
                    (timezone_name, cutoff),
                ).rowcount
                db.execute(
                    "DELETE FROM activity_corrections WHERE timezone=? AND local_date < ?",
                    (timezone_name, cutoff),
                )
                self._purged_dates[timezone_name] = local_today
            return deleted

    def delete_all(self) -> bool:
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM daily_activity")
            db.execute("DELETE FROM activity_corrections")
        return True


class ActivitySummaryRecorder:
    """Convert short, clock-checked live intervals into local daily totals."""

    def __init__(self, store: ActivityHistoryStore, *, timezone_name: str,
                 max_sample_gap_seconds: float = MAX_SAMPLE_GAP_SECONDS,
                 sample_interval_seconds: float = 2.0):
        if not isinstance(store, ActivityHistoryStore):
            raise TypeError("store must be an ActivityHistoryStore")
        self.store = store
        self.timezone_name = timezone_name
        self._zone = _timezone(timezone_name)
        if (isinstance(max_sample_gap_seconds, bool) or not isinstance(max_sample_gap_seconds, (int, float))
                or not math.isfinite(max_sample_gap_seconds) or max_sample_gap_seconds <= 0
                or max_sample_gap_seconds > MAX_SAMPLE_GAP_SECONDS):
            raise ValueError("sample gap must be positive and no greater than five seconds")
        self.max_sample_gap_seconds = float(max_sample_gap_seconds)
        if (isinstance(sample_interval_seconds, bool) or not isinstance(sample_interval_seconds, (int, float))
                or not math.isfinite(sample_interval_seconds) or sample_interval_seconds <= 0
                or sample_interval_seconds >= self.max_sample_gap_seconds):
            raise ValueError("sample interval must be positive and shorter than the maximum sample gap")
        self.sample_interval_seconds = float(sample_interval_seconds)
        self._previous = None
        self._was_enabled = False

    def record(self, snapshot, *, wall_time: datetime, monotonic_time: float) -> bool:
        wall_now = _aware(wall_time, "wall_time")
        if isinstance(monotonic_time, bool) or not isinstance(monotonic_time, (int, float)) or not math.isfinite(monotonic_time):
            raise ValueError("monotonic_time must be finite")
        if not hasattr(snapshot, "fields") or not isinstance(snapshot.fields, dict):
            raise TypeError("snapshot must provide context fields")
        enabled = self.store.enabled
        current = (snapshot, wall_now, float(monotonic_time))
        if not enabled:
            self._previous = current
            self._was_enabled = False
            return False
        if not self._was_enabled:
            self._previous = current
            self._was_enabled = True
            return False
        previous = self._previous
        if previous is None:
            self._previous = current
            return False
        if monotonic_time - previous[2] < self.sample_interval_seconds:
            return False
        self._previous = current
        prior_snapshot, prior_wall, prior_mono = previous
        elapsed_mono = monotonic_time - prior_mono
        elapsed_wall = (wall_now - prior_wall).total_seconds()
        if (elapsed_mono <= 0 or elapsed_mono > self.max_sample_gap_seconds
                or elapsed_wall <= 0
                or abs(elapsed_wall - elapsed_mono) > MAX_CLOCK_DRIFT_SECONDS):
            return False
        interval_end = min(wall_now, prior_wall + timedelta(seconds=elapsed_mono))
        if interval_end <= prior_wall:
            return False

        prior_fields = prior_snapshot.fields
        current_fields = snapshot.fields
        focus = self._field_duration(prior_fields.get("focusSession"), prior_wall, interval_end,
                                     eligible_values={"focus", "background"})
        break_field = prior_fields.get("declaredIntent")
        break_coverage = self._field_duration(break_field, prior_wall, interval_end,
                                              eligible_values={"focus", "break", "research", "meeting", "none"})
        break_seconds = break_coverage if getattr(break_field, "value", None) == "break" else 0.0
        category = prior_fields.get("foregroundCategory")
        desktop = prior_fields.get("desktopActivity")
        category_coverage = 0.0
        entertainment = 0.0
        if (getattr(desktop, "value", None) in {"input_active", "input_idle"}
                and getattr(current_fields.get("desktopActivity"), "value", None) in {"input_active", "input_idle"}):
            category_coverage = min(
                self._field_duration(category, prior_wall, interval_end,
                                     eligible_values={"development", "communication", "browser_unspecified", "entertainment", "other"}),
                self._field_duration(desktop, prior_wall, interval_end,
                                     eligible_values={"input_active", "input_idle"}),
            )
            if getattr(category, "value", None) == "entertainment":
                entertainment = category_coverage

        if getattr(snapshot, "paused", False):
            focus = break_coverage = break_seconds = category_coverage = entertainment = 0.0
        total = int(round(elapsed_mono))
        tracked_remaining = total
        for segment_day, exact_seconds, is_last in self._split_days(prior_wall, interval_end):
            segment = tracked_remaining if is_last else min(tracked_remaining, int(math.floor(exact_seconds + 1e-8)))
            tracked_remaining -= segment
            focus_part = min(segment, int(math.floor(focus + 1e-8)))
            break_part = min(segment, int(math.floor(break_seconds + 1e-8)))
            category_part = min(segment, int(math.floor(entertainment + 1e-8)))
            focus_cov = min(segment, int(math.floor(focus + 1e-8)))
            break_cov = min(segment, int(math.floor(break_coverage + 1e-8)))
            category_cov = min(segment, int(math.floor(category_coverage + 1e-8)))
            focus -= focus_part
            break_seconds -= break_part
            break_coverage -= break_cov
            entertainment -= category_part
            category_coverage -= category_cov
            self.store.add_interval(
                segment_day, self.timezone_name, tracked_seconds=max(0, segment),
                focus_session_seconds=focus_part, focus_coverage_seconds=focus_cov,
                declared_break_seconds=break_part, break_coverage_seconds=break_cov,
                entertainment_category_seconds=category_part,
                entertainment_coverage_seconds=category_cov,
            )
        return total > 0

    @staticmethod
    def _field_duration(field, start: datetime, end: datetime, *, eligible_values: set[str]) -> float:
        if (field is None or getattr(field, "freshness", None) != "fresh"
                or getattr(field, "source_available", False) is not True
                or getattr(field, "value", None) not in eligible_values):
            return 0.0
        expires = getattr(field, "expires_at", None)
        if not isinstance(expires, datetime) or expires.tzinfo is None or expires.utcoffset() is None:
            return 0.0
        return max(0.0, (min(end, expires.astimezone(timezone.utc)) - start).total_seconds())

    def _split_days(self, start: datetime, end: datetime):
        cursor_utc = start.astimezone(timezone.utc)
        finish_utc = end.astimezone(timezone.utc)
        segments = []
        while cursor_utc < finish_utc:
            local_cursor = cursor_utc.astimezone(self._zone) if self._zone is not None else cursor_utc.astimezone()
            tomorrow = datetime.combine(local_cursor.date() + timedelta(days=1), time.min)
            if self._zone is not None:
                tomorrow = tomorrow.replace(tzinfo=self._zone)
            else:
                tomorrow = tomorrow.astimezone()
            tomorrow_utc = tomorrow.astimezone(timezone.utc)
            segment_end_utc = min(finish_utc, tomorrow_utc)
            segments.append((local_cursor.date(), (segment_end_utc - cursor_utc).total_seconds(),
                             segment_end_utc == finish_utc))
            cursor_utc = segment_end_utc
        return segments


def _format_minutes(seconds: int) -> str:
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes}m {remainder:02d}s"


def render_daily_summary(store: ActivityHistoryStore, *, timezone_name: str,
                         now: datetime | None = None) -> str:
    """Render only persisted facts; never ask the LLM to calculate daily totals."""
    if not isinstance(store, ActivityHistoryStore):
        raise TypeError("store must be an ActivityHistoryStore")
    if not store.enabled:
        return "Activity history is off. Enable daily reflection in the local dashboard to collect summaries."
    current = _aware(now or datetime.now(timezone.utc), "now")
    zone = _timezone(timezone_name)
    local_today = current.astimezone(zone).date()
    summary = store.get_summary(local_today, timezone_name=timezone_name)
    if summary is None:
        return f"No measured activity summary exists for {local_today.isoformat()} yet. Scoring is disabled."
    return (
        f"{summary.local_date} ({summary.timezone}) — "
        f"Tracked focus-session minutes: {_format_minutes(summary.focus_session_seconds)} "
        f"(covered {_format_minutes(summary.focus_coverage_seconds)}, "
        f"uncovered {_format_minutes(summary.focus_uncovered_seconds)}); "
        f"Declared break minutes: {_format_minutes(summary.declared_break_seconds)} "
        f"(covered {_format_minutes(summary.break_coverage_seconds)}, "
        f"uncovered {_format_minutes(summary.break_uncovered_seconds)}); "
        f"Observed entertainment-category minutes: {_format_minutes(summary.entertainment_category_seconds)} "
        f"(covered {_format_minutes(summary.entertainment_coverage_seconds)}, "
        f"uncovered {_format_minutes(summary.entertainment_uncovered_seconds)}). "
        f"Total tracked coverage: {_format_minutes(summary.tracked_seconds)}. "
        "Scoring is disabled."
    )
