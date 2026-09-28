from dataclasses import replace
from datetime import datetime, timedelta, timezone

from assistant.companion_policy import InterventionIntent
from assistant.intervention_delivery import InterventionDelivery


def intent(intent_id="intent-1", *, expires_at=None):
    return InterventionIntent(
        intent_id=intent_id, deduplication_key=f"desk:{intent_id}",
        rule_id="desk_check_in", trigger_id="confirmed_desk_absence",
        evidence_timestamps=(), reason_code="confirmed_desk_absence",
        expires_at=expires_at or datetime(2026, 9, 27, 10, 1, tzinfo=timezone.utc),
        priority=20, allowed_channels=("desktop_speech",),
        message="Want to pause your focus session?",
    )


def test_one_intent_is_dispatched_only_once():
    now = datetime(2026, 9, 27, 10, tzinfo=timezone.utc)
    delivery = InterventionDelivery(now=lambda: now)
    sent = []

    first = delivery.dispatch(intent(), revalidate=lambda: True,
                              send=lambda text, guard: sent.append((text, guard)) or True)
    second = delivery.dispatch(intent(), revalidate=lambda: True,
                               send=lambda text, guard: sent.append((text, guard)) or True)

    assert first.status == "attempted"
    assert second.status == "attempted"
    assert len(sent) == 1


def test_expired_or_no_longer_eligible_intent_is_not_queued():
    now = datetime(2026, 9, 27, 10, tzinfo=timezone.utc)
    delivery = InterventionDelivery(now=lambda: now)
    sent = []

    expired = delivery.dispatch(
        intent("expired", expires_at=now - timedelta(seconds=1)),
        revalidate=lambda: True,
        send=lambda text, guard: sent.append(text) or True,
    )
    suppressed = delivery.dispatch(
        intent("changed"), revalidate=lambda: False,
        send=lambda text, guard: sent.append(text) or True,
    )

    assert expired.status == "expired"
    assert suppressed.status == "suppressed"
    assert sent == []


def test_queued_speech_rechecks_current_conditions_at_playback_time():
    now = [datetime(2026, 9, 27, 10, tzinfo=timezone.utc)]
    delivery = InterventionDelivery(now=lambda: now[0])
    currently_eligible = [True]
    queued = []

    receipt = delivery.dispatch(
        intent(), revalidate=lambda: currently_eligible[0],
        send=lambda text, guard: queued.append((text, guard)) or True,
    )
    assert receipt.status == "attempted"
    assert queued[0][1]() is True

    currently_eligible[0] = False

    assert queued[0][1]() is False
    assert delivery.receipt("intent-1").status == "suppressed"


def test_channel_budget_is_shared_and_excess_intents_are_suppressed():
    now = [datetime(2026, 9, 27, 10, tzinfo=timezone.utc)]
    monotonic = [100.0]
    delivery = InterventionDelivery(
        now=lambda: now[0], monotonic=lambda: monotonic[0],
        min_interval_seconds=60, max_per_hour=2,
    )
    statuses = []

    for index in range(3):
        item = intent(f"budget-{index}")
        item = replace(item, expires_at=now[0] + timedelta(minutes=1))
        statuses.append(delivery.dispatch(
            item, revalidate=lambda: True,
            send=lambda text, guard: True,
        ).status)
        now[0] += timedelta(minutes=1)
        monotonic[0] += 60.0

    assert statuses == ["attempted", "attempted", "suppressed"]


def test_forward_wall_clock_jump_cannot_bypass_cooldown():
    now = [datetime(2026, 9, 27, 10, tzinfo=timezone.utc)]
    monotonic = [100.0]
    delivery = InterventionDelivery(now=lambda: now[0], monotonic=lambda: monotonic[0])
    first = intent("clock-1", expires_at=now[0] + timedelta(minutes=1))
    delivery.dispatch(first, revalidate=lambda: True, send=lambda text, guard: True)

    now[0] += timedelta(days=1)
    monotonic[0] += 30.0
    second = intent("clock-2", expires_at=now[0] + timedelta(minutes=1))

    result = delivery.dispatch(second, revalidate=lambda: True,
                               send=lambda text, guard: True)

    assert result.status == "suppressed"
    assert result.reason_code == "cooldown"


def test_durable_intent_claim_prevents_repeat_alert_after_runtime_restart(tmp_path):
    now = datetime(2026, 9, 27, 10, tzinfo=timezone.utc)
    path = tmp_path / "delivery-receipts.sqlite3"
    first_delivery = InterventionDelivery(now=lambda: now, receipt_path=path)
    first = first_delivery.dispatch(intent("stable-agent-operation"), revalidate=lambda: True,
                                    send=lambda text, guard: True)
    first_delivery.close()
    second_delivery = InterventionDelivery(now=lambda: now + timedelta(seconds=5), receipt_path=path)
    sent = []

    second = second_delivery.dispatch(
        intent("stable-agent-operation"), revalidate=lambda: True,
        send=lambda text, guard: sent.append(text) or True,
    )

    assert first.status == "attempted"
    assert second.status == "suppressed"
    assert second.reason_code == "duplicate_previous_run"
    assert sent == []
    second_delivery.close()

