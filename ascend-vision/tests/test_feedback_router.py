"""Unit tests for the AI Diva feedback router and phone detection trigger."""
from unittest.mock import MagicMock
import pytest

from expression_tracker import ExpressionTracker
from feedback_router import (
    USER_NAME,
    PHONE_REACTION_COOLDOWN,
    DIVA_PHONE_FALLBACKS,
    DIVA_SCROLLING_FALLBACKS,
    DIVA_GAMING_FALLBACKS,
    DIVA_CALL_FALLBACKS,
    DIVA_STUDYING_FALLBACKS,
    DIVA_STREAM_FALLBACKS,
    DIVA_SCREEN_GAMING_FALLBACKS,
    EXPRESSION_PROMPT_STRATEGIES,
    FeedbackRouter,
    get_expression_prompt_context,
    get_fallback_expression_reply,
)


def test_user_name_is_cb():
    assert USER_NAME == "CB"


def test_phone_prompt_context_contains_diva_instructions():
    context = get_expression_prompt_context("phone_detected")
    assert "CB just got distracted and looked at their phone" in context
    assert "AI Diva roast" in context
    assert "under 18 words" in context

    # Check alternative alias
    context_alias = get_expression_prompt_context("PHONE_USE")
    assert "CB just got distracted and looked at their phone" in context_alias


def test_phone_orientation_prompts_and_fallbacks():
    # Scrolling
    scroll_ctx = get_expression_prompt_context("PHONE_SCROLLING")
    assert "scrolling feeds" in scroll_ctx
    assert "under 18 words" in scroll_ctx
    scroll_fallback = get_fallback_expression_reply("PHONE_SCROLLING")
    assert scroll_fallback in DIVA_SCROLLING_FALLBACKS
    assert "CB" in scroll_fallback

    # Gaming
    game_ctx = get_expression_prompt_context("PHONE_GAMING")
    assert "landscape mode to play games" in game_ctx
    assert "under 18 words" in game_ctx
    game_fallback = get_fallback_expression_reply("PHONE_GAMING")
    assert game_fallback in DIVA_GAMING_FALLBACKS
    assert "CB" in game_fallback

    # Call
    call_ctx = get_expression_prompt_context("PHONE_CALL")
    assert "phone up to their ear" in call_ctx
    call_fallback = get_fallback_expression_reply("PHONE_CALL")
    assert call_fallback in DIVA_CALL_FALLBACKS
    assert "CB" in call_fallback


def test_screen_audit_prompts_and_fallbacks():
    # Studying / Coding
    study_ctx = get_expression_prompt_context("screen_studying_coding")
    assert "studying, writing code" in study_ctx
    assert "under 18 words" in study_ctx
    study_reply = get_fallback_expression_reply("screen_studying_coding")
    assert study_reply in DIVA_STUDYING_FALLBACKS
    assert "CB" in study_reply

    # Watching Stream / Video
    stream_ctx = get_expression_prompt_context("screen_watching_video")
    assert "watching YouTube, streams" in stream_ctx
    assert "under 18 words" in stream_ctx
    stream_reply = get_fallback_expression_reply("screen_watching_video")
    assert stream_reply in DIVA_STREAM_FALLBACKS
    assert "CB" in stream_reply

    # Gaming
    gaming_ctx = get_expression_prompt_context("screen_gaming")
    assert "playing a video game" in gaming_ctx
    assert "under 18 words" in gaming_ctx
    gaming_reply = get_fallback_expression_reply("screen_gaming")
    assert gaming_reply in DIVA_SCREEN_GAMING_FALLBACKS
    assert "CB" in gaming_reply


def test_phone_fallback_replies():
    for _ in range(20):
        reply = get_fallback_expression_reply("phone_detected")
        assert reply in DIVA_PHONE_FALLBACKS
        assert "CB" in reply


def test_fatigue_and_expression_prompts():
    assert "CB just yawned" in get_expression_prompt_context("fatigue")
    assert "CB looks stressed" in get_expression_prompt_context("stressed")
    assert "CB is smiling" in get_expression_prompt_context("smiling")

    assert "Yawning in front of a star" in get_fallback_expression_reply("fatigue")
    assert "Wrinkles are not the look" in get_fallback_expression_reply("stressed")
    assert "my genius is breathtaking" in get_fallback_expression_reply("smiling")


def test_feedback_router_cooldown():
    mock_router = MagicMock()
    mock_router.generate_response.return_value = "Put that screen down, CB!"

    router = FeedbackRouter(router=mock_router, phone_cooldown=60.0)

    # First trigger at t=100.0 should succeed
    res1 = router.route_reaction("phone_detected", timestamp=100.0)
    assert res1 == "Put that screen down, CB!"
    assert mock_router.generate_response.call_count == 1

    # Second trigger at t=130.0 (< 60s cooldown) should be suppressed
    res2 = router.route_reaction("phone_detected", timestamp=130.0)
    assert res2 is None
    assert mock_router.generate_response.call_count == 1

    # Third trigger at t=161.0 (> 60s cooldown) should succeed
    res3 = router.route_reaction("phone_detected", timestamp=161.0)
    assert res3 == "Put that screen down, CB!"
    assert mock_router.generate_response.call_count == 2


def test_expression_tracker_phone_debounce_and_cooldown():
    tracker = ExpressionTracker(
        phone_threshold_duration=2.0,
        phone_cooldown_seconds=60.0
    )

    t = 100.0
    # Frame 1 at t=100.0: phone detected for 0.0s -> no trigger
    assert tracker.update_phone(True, timestamp=t) is None

    # Frame 2 at t=101.5: phone detected for 1.5s (< 2.0s) -> no trigger
    assert tracker.update_phone(True, timestamp=t + 1.5) is None

    # Frame 3 at t=102.1: phone detected for 2.1s (>= 2.0s) -> triggers!
    assert tracker.update_phone(True, timestamp=t + 2.1) == 'phone_detected'

    # Frame 4 at t=103.0: still holding in same streak -> no re-trigger
    assert tracker.update_phone(True, timestamp=t + 3.0) is None

    # Phone put away at t=105.0
    assert tracker.update_phone(False, timestamp=t + 5.0) is None

    # Phone picked up again at t=110.0 and sustained until t=113.0 (< 60s from t=102.1) -> cooldown suppresses
    assert tracker.update_phone(True, timestamp=t + 110.0) is None
    # Wait, t + 110.0 is 210.0, which is > 60s! Let's use t + 20.0
    tracker._last_phone_trigger_time = 102.1
    tracker._states['phone_detected'].reset()

    # Sustained at t=120.0 to t=123.0 (diff = 20.9s < 60s)
    tracker.update_phone(True, timestamp=120.0)
    assert tracker.update_phone(True, timestamp=123.0) is None

    # After 60s cooldown (t=165.0, diff = 62.9s > 60s)
    tracker._states['phone_detected'].reset()
    tracker.update_phone(True, timestamp=165.0)
    assert tracker.update_phone(True, timestamp=167.5) == 'phone_detected'
