"""Local orchestration that connects fresh context, policy, delivery, and speech."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable

from assistant.companion_policy import CompanionPolicy, CompanionPreferences, PolicyEvaluation
from assistant.intervention_delivery import DeliveryReceipt, InterventionDelivery


@dataclass(frozen=True)
class CompanionTick:
    evaluation: PolicyEvaluation
    receipts: tuple[DeliveryReceipt, ...]


class CompanionRuntime:
    """Evaluate locally and enqueue no more than one guarded speech item per tick."""

    def __init__(self, context_provider, policy: CompanionPolicy,
                 delivery: InterventionDelivery,
                 preferences: Callable[[], CompanionPreferences], speech,
                 *, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 shadow_mode: bool = False, agent_needs_input_provider=lambda: (), notification_publisher=None):
        if not callable(getattr(context_provider, "read_snapshot", None)):
            raise TypeError("context_provider must provide read_snapshot")
        if not isinstance(policy, CompanionPolicy):
            raise TypeError("policy must be a CompanionPolicy")
        if not isinstance(delivery, InterventionDelivery):
            raise TypeError("delivery must be an InterventionDelivery")
        if not callable(preferences):
            raise TypeError("preferences must be callable")
        if not callable(getattr(speech, "speak_guarded_announcement", None)):
            raise TypeError("speech must provide speak_guarded_announcement")
        if type(shadow_mode) is not bool:
            raise ValueError("shadow_mode must be a boolean")
        if not callable(agent_needs_input_provider):
            raise TypeError("agent_needs_input_provider must be callable")
        self._context_provider = context_provider
        self._policy = policy
        self._delivery = delivery
        self._preferences = preferences
        self._speech = speech
        self._now = now
        self._shadow_mode = shadow_mode
        self._agent_needs_input_provider = agent_needs_input_provider
        self._notification_publisher = notification_publisher

    def tick(self) -> CompanionTick:
        snapshot = self._context_provider.read_snapshot()
        preferences = self._preferences()
        if self._shadow_mode:
            preferences = replace(preferences, local_speech_enabled=True)
        now = self._aware_now()
        evaluation = self._policy.evaluate(
            snapshot, preferences, now, agent_needs_input=self._read_agent_needs_input(),
        )
        if not self._shadow_mode and self._notification_publisher is not None:
            for intent in evaluation.intents:
                if intent.rule_id == "agent_needs_input":
                    try:
                        self._notification_publisher.publish(intent)
                    except Exception:
                        # Remote notification failure must never affect local context or speech.
                        pass
        if self._shadow_mode:
            self._record_decision(evaluation, now)
            return CompanionTick(evaluation, ())
        receipts = []
        for intent in sorted(evaluation.intents, key=lambda candidate: candidate.priority, reverse=True):
            if self._delivery.receipt(intent.intent_id) is not None:
                continue
            receipt = self._delivery.dispatch(
                intent,
                revalidate=lambda target=intent.intent_id: self._is_currently_eligible(target),
                send=self._speech.speak_guarded_announcement,
            )
            receipts.append(receipt)
            if receipt.status in {"claimed", "attempted"}:
                break
        self._record_decision(evaluation, now, receipts)
        return CompanionTick(evaluation, tuple(receipts))

    def _record_decision(self, evaluation, now, receipts=()):
        record = getattr(self._context_provider, "record_companion_decision", None)
        if not callable(record):
            return
        intents = {intent.intent_id: intent for intent in evaluation.intents}
        receipt_by_id = {receipt.intent_id: receipt for receipt in receipts}
        rules = []
        for decision in evaluation.decisions:
            intent = intents.get(decision.intent_id)
            receipt = receipt_by_id.get(decision.intent_id)
            source = evidence_age = None
            if intent is not None and intent.evidence_timestamps:
                source, observed_at = intent.evidence_timestamps[0]
                evidence_age = max(0, int((now - observed_at).total_seconds()))
            rules.append({
                "rule_id": decision.rule_id,
                "trigger": decision.reason_code,
                "source": source,
                "evidence_age_seconds": evidence_age,
                "channel": receipt.channel if receipt else (
                    intent.allowed_channels[0] if intent and intent.allowed_channels else None
                ),
                "outcome": receipt.status if receipt else (
                    "shadowed" if self._shadow_mode and intent else "not_eligible"
                ),
                "reason_code": receipt.reason_code if receipt else decision.reason_code,
            })
        record({"evaluated_at": now.isoformat(),
                "mode": "shadow" if self._shadow_mode else "active", "rules": rules})

    def _is_currently_eligible(self, intent_id: str) -> bool:
        try:
            snapshot = self._context_provider.read_snapshot()
            preferences = self._preferences()
            # FeedbackService is speaking this guarded job when the callback runs.
            preferences = replace(preferences, vision_speaking=False)
            evaluation = self._policy.evaluate(
                snapshot, preferences, self._aware_now(),
                agent_needs_input=self._read_agent_needs_input(),
            )
            return any(intent.intent_id == intent_id for intent in evaluation.intents)
        except Exception:
            return False

    def _aware_now(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("companion clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    def _read_agent_needs_input(self):
        try:
            value = self._agent_needs_input_provider()
            return value if isinstance(value, tuple) else ()
        except Exception:
            return ()
