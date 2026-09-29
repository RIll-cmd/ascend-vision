"""Conservative, provider-specific budget reservations for browser model calls."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import secrets
import sqlite3
import threading
from datetime import date, datetime, timezone
from urllib.parse import urlsplit


class PricingUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelPrice:
    provider: str
    model: str
    input_usd_per_million: int
    output_usd_per_million: int
    version: str
    source_url: str

    def __post_init__(self):
        if self.provider not in {'gemini', 'cerebras', 'groq'}:
            raise ValueError('unsupported provider')
        if not self.model or len(self.model) > 128 or not self.version or len(self.version) > 64:
            raise ValueError('price model or version is invalid')
        for name in ('input_usd_per_million', 'output_usd_per_million'):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f'{name} must be a non-negative integer number of micros')
        if not isinstance(self.source_url, str) or not self.source_url.startswith('https://') or len(self.source_url) > 500:
            raise ValueError('price source must be an HTTPS URL')
        host = (urlsplit(self.source_url).hostname or '').lower().rstrip('.')
        path = urlsplit(self.source_url).path.lower()
        official_hosts = {'ai.google.dev', 'groq.com', 'www.groq.com', 'cerebras.ai', 'www.cerebras.ai'}
        if host not in official_hosts or 'pricing' not in path:
            raise ValueError('price source must be an official provider pricing page')


class PriceTable:
    def __init__(self, version: str, prices: list[ModelPrice], effective_date: str, currency: str):
        if not isinstance(version, str) or not version or len(version) > 64:
            raise ValueError('price table version is invalid')
        try:
            parsed_date = datetime.fromisoformat(effective_date).date()
        except (TypeError, ValueError) as exc:
            raise ValueError('price table effective_date must be YYYY-MM-DD') from exc
        if len(effective_date) != 10 or parsed_date.isoformat() != effective_date:
            raise ValueError('price table effective_date must be YYYY-MM-DD')
        if currency != 'USD':
            raise ValueError('price table currency must be USD')
        if not isinstance(prices, list) or len(prices) > 100:
            raise ValueError('price table must contain at most 100 entries')
        self.version = version
        self.effective_date = effective_date
        self.currency = currency
        self._prices = {}
        for item in prices:
            if not isinstance(item, ModelPrice):
                raise TypeError('prices must contain ModelPrice entries')
            key = (item.provider, item.model)
            if key in self._prices:
                raise ValueError('price table contains a duplicate model')
            self._prices[key] = item

    def require_fresh(self, *, as_of: date | None = None, max_age_days: int = 30) -> None:
        if type(max_age_days) is not int or not 1 <= max_age_days <= 30:
            raise ValueError('price freshness window is invalid')
        effective = date.fromisoformat(self.effective_date)
        current = as_of or datetime.now(timezone.utc).date()
        age = (current - effective).days
        if age < 0 or age > max_age_days:
            raise PricingUnavailable('price table is future-dated or older than 30 days')

    @classmethod
    def from_json(cls, path: str | Path) -> 'PriceTable':
        try:
            payload = json.loads(Path(path).read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError('price table could not be loaded') from exc
        if not isinstance(payload, dict) or set(payload) != {'version', 'effective_date', 'currency', 'prices'} or not isinstance(payload['prices'], list):
            raise ValueError('price table has an invalid schema')
        entries = []
        for raw in payload['prices']:
            fields = {'provider', 'model', 'input_usd_micros_per_million',
                      'output_usd_micros_per_million', 'source_url'}
            if not isinstance(raw, dict) or set(raw) != fields:
                raise ValueError('price table entry has an invalid schema')
            entries.append(ModelPrice(raw['provider'], raw['model'], raw['input_usd_micros_per_million'],
                                      raw['output_usd_micros_per_million'], payload['version'], raw['source_url']))
        return cls(payload['version'], entries, payload['effective_date'], payload['currency'])

    def estimate_micros(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> int:
        price = self._prices.get((provider, model))
        if price is None:
            raise PricingUnavailable('selected provider/model is not covered by the verified price table')
        if any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens)):
            raise ValueError('reservation token counts must be non-negative integers')
        return ((price.input_usd_per_million * input_tokens
                 + price.output_usd_per_million * output_tokens + 999_999) // 1_000_000)


@dataclass(frozen=True)
class UsageReservation:
    reservation_id: str
    run_id: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd_micros: int
    price_table_version: str


class BudgetTracker:
    """Tracks task token limits and reserves pilot cost before each provider attempt."""

    MAX_PILOT_ATTEMPTS = 10_000

    def __init__(self, price_table: PriceTable | None, *, max_input_tokens: int = 50_000,
                 max_output_tokens: int = 8_000, daily_cost_usd_micros: int = 1_000_000,
                 pilot_cost_usd_micros: int = 5_000_000, ledger_path: str | Path | None = None):
        for name, value, ceiling in (
            ('max_input_tokens', max_input_tokens, 50_000),
            ('max_output_tokens', max_output_tokens, 8_000),
            ('daily_cost_usd_micros', daily_cost_usd_micros, 1_000_000),
            ('pilot_cost_usd_micros', pilot_cost_usd_micros, 5_000_000),
        ):
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError(f'{name} is outside its safe limit')
        self.price_table = price_table
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens
        self.daily_cost_usd_micros = daily_cost_usd_micros
        self.pilot_cost_usd_micros = pilot_cost_usd_micros
        self.ledger_path = Path(ledger_path).resolve() if ledger_path is not None else None
        self._lock = threading.Lock()
        self._tokens: dict[str, list[int]] = {}
        self._cost = 0
        self._daily_cost = 0
        self._daily_date = datetime.now(timezone.utc).date()
        self._reserved: dict[str, tuple[str, int]] = {}
        if self.ledger_path is not None:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self._ledger() as db:
                db.execute('''CREATE TABLE IF NOT EXISTS attempts(
                    attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, day TEXT NOT NULL,
                    provider TEXT NOT NULL, model TEXT NOT NULL, reserved_usd_micros INTEGER NOT NULL)''')
                db.commit()

    def _ledger(self):
        if self.ledger_path is None:
            raise RuntimeError('persistent pilot budget ledger is unavailable')
        db = sqlite3.connect(self.ledger_path, timeout=3, isolation_level='IMMEDIATE')
        db.execute('PRAGMA busy_timeout=3000')
        return db

    def reserve(self, run_id: str, provider: str, model: str, *,
                conservative_input_tokens: int | None, max_output_tokens: int,
                attempt_id: str | None = None) -> UsageReservation:
        if not run_id or len(run_id) > 128:
            raise ValueError('run_id is invalid')
        if conservative_input_tokens is None:
            raise PricingUnavailable('a conservative provider token bound is unavailable')
        if (type(conservative_input_tokens) is not int or not 0 <= conservative_input_tokens <= self.max_input_tokens
                or type(max_output_tokens) is not int or not 1 <= max_output_tokens <= self.max_output_tokens):
            raise ValueError('provider call exceeds the configured token budget')
        if self.price_table is None:
            raise PricingUnavailable('a reviewed, current price table is required for paid pilot calls')
        cost = self.price_table.estimate_micros(provider, model, conservative_input_tokens, max_output_tokens)
        with self._lock:
            today = datetime.now(timezone.utc).date()
            if today != self._daily_date:
                self._daily_date = today
                self._daily_cost = 0
            totals = self._tokens.setdefault(run_id, [0, 0])
            if totals[0] + conservative_input_tokens > self.max_input_tokens:
                raise PricingUnavailable('task input-token reservation exceeds its limit')
            if totals[1] + max_output_tokens > self.max_output_tokens:
                raise PricingUnavailable('task output-token reservation exceeds its limit')
            reservation_id = attempt_id or secrets.token_urlsafe(18)
            if not isinstance(reservation_id, str) or not 16 <= len(reservation_id) <= 128:
                raise ValueError('attempt_id is invalid')
            if self.ledger_path is not None:
                day = today.isoformat()
                with self._ledger() as db:
                    prior = db.execute('SELECT run_id,provider,model,reserved_usd_micros FROM attempts WHERE attempt_id=?',
                                       (reservation_id,)).fetchone()
                    if prior is not None:
                        if prior != (run_id, provider, model, cost):
                            raise PricingUnavailable('provider attempt ID is already bound to a different reservation')
                        existing_cost = prior[3]
                    else:
                        count = db.execute('SELECT count(*) FROM attempts').fetchone()[0]
                        if count >= self.MAX_PILOT_ATTEMPTS:
                            raise PricingUnavailable('the pilot provider-attempt limit has been reached')
                        daily = db.execute('SELECT COALESCE(sum(reserved_usd_micros),0) FROM attempts WHERE day=?',
                                           (day,)).fetchone()[0]
                        total = db.execute('SELECT COALESCE(sum(reserved_usd_micros),0) FROM attempts').fetchone()[0]
                        if daily + cost > self.daily_cost_usd_micros:
                            raise PricingUnavailable('next provider call exceeds the daily cost target')
                        if total + cost > self.pilot_cost_usd_micros:
                            raise PricingUnavailable('next provider call exceeds the configured cost target')
                        db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?)',
                                   (reservation_id, run_id, day, provider, model, cost))
                        db.commit()
                        existing_cost = cost
            else:
                committed = self._cost + sum(value[1] for value in self._reserved.values())
                daily_reserved = sum(value[1] for value in self._reserved.values())
                if self._daily_cost + daily_reserved + cost > self.daily_cost_usd_micros:
                    raise PricingUnavailable('next provider call exceeds the daily cost target')
                if committed + cost > self.pilot_cost_usd_micros:
                    raise PricingUnavailable('next provider call exceeds the configured cost target')
                self._reserved[reservation_id] = (run_id, cost)
                existing_cost = cost
            totals[0] += conservative_input_tokens
            totals[1] += max_output_tokens
        return UsageReservation(reservation_id, run_id, provider, model, conservative_input_tokens, max_output_tokens,
                                existing_cost, self.price_table.version)

    def settle(self, reservation: UsageReservation, *, actual_cost_usd_micros: int | None) -> None:
        if not isinstance(reservation, UsageReservation):
            raise TypeError('reservation must be UsageReservation')
        with self._lock:
            if self.ledger_path is not None:
                # The durable ledger charged the full reservation before the request.
                # It deliberately keeps this conservative amount across restarts.
                return
            # Unknown usage retains the full conservative reservation for this pilot.
            amount = reservation.cost_usd_micros if actual_cost_usd_micros is None else actual_cost_usd_micros
            if type(amount) is not int or amount < 0:
                raise ValueError('actual cost is invalid')
            self._cost += amount
            self._daily_cost += amount
            self._reserved.pop(reservation.reservation_id, None)

    def forget(self, run_id: str) -> None:
        """Release per-task token counters while retaining cumulative pilot spend."""
        with self._lock:
            self._tokens.pop(run_id, None)
            for key, value in tuple(self._reserved.items()):
                if value[0] == run_id:
                    self._reserved.pop(key, None)
