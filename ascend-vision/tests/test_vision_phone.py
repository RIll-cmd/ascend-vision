"""Unit tests for phone orientation classification and posture detection."""
import pytest

from detector import PhoneBox
from expression_tracker import ExpressionTracker
from posture import classify_phone_orientation, is_near_ear, is_in_hand


def test_aspect_ratio_scrolling():
    """Verify h=300, w=150 -> aspect_ratio = 2.0 -> PHONE_SCROLLING."""
    # (x1, y1, x2, y2): width = 150, height = 300
    box = PhoneBox((100.0, 100.0, 250.0, 400.0), 0.95)
    posture = classify_phone_orientation(box)
    assert posture == "PHONE_SCROLLING"

    # Also test tuple directly
    assert classify_phone_orientation((0, 0, 100, 200)) == "PHONE_SCROLLING"


def test_aspect_ratio_gaming():
    """Verify h=150, w=300 -> aspect_ratio = 0.5 -> PHONE_GAMING."""
    # (x1, y1, x2, y2): width = 300, height = 150
    box = PhoneBox((100.0, 100.0, 400.0, 250.0), 0.95)
    posture = classify_phone_orientation(box)
    assert posture == "PHONE_GAMING"

    # Also test tuple directly
    assert classify_phone_orientation((0, 0, 200, 100)) == "PHONE_GAMING"


def test_aspect_ratio_ambiguous():
    """Verify square / tilted ratio -> PHONE_USE."""
    box = PhoneBox((100.0, 100.0, 200.0, 200.0), 0.95)  # 100x100 -> ratio 1.0
    posture = classify_phone_orientation(box)
    assert posture == "PHONE_USE"


def test_calling_detection():
    """Verify phone box adjacent to face landmark bbox -> PHONE_CALL."""
    # Mock face landmarks spanning x: 200-300, y: 100-250
    face_landmarks = [
        (200.0, 100.0, 0.0),
        (250.0, 150.0, 0.0),
        (300.0, 250.0, 0.0),
    ] + [(250.0, 180.0, 0.0)] * 10

    # Phone held to ear on right side: x1=280, y1=120, x2=340, y2=240
    ear_phone = PhoneBox((280.0, 120.0, 340.0, 240.0), 0.92)
    posture = classify_phone_orientation(ear_phone, face_landmarks=face_landmarks)
    assert posture == "PHONE_CALL"


def test_hand_interaction_filtering():
    """Verify when hands list is provided, phone must be near a hand."""
    box = PhoneBox((100.0, 100.0, 250.0, 400.0), 0.95)  # vertical
    # Empty hands list -> not in hand
    assert classify_phone_orientation(box, hands=[]) == "NONE"

    # Hands far away -> not in hand
    distant_hand = [[(800.0, 800.0, 0.0)]]
    assert classify_phone_orientation(box, hands=distant_hand) == "NONE"

    # Hand overlapping phone
    overlapping_hand = [[(150.0, 200.0, 0.0)]]
    assert classify_phone_orientation(box, hands=overlapping_hand) == "PHONE_SCROLLING"


def test_phone_posture_debouncing_and_cooldown():
    """Test 2.0s continuous hold debouncing and per-event cooldown in ExpressionTracker."""
    tracker = ExpressionTracker(phone_threshold_duration=2.0, phone_cooldown_seconds=60.0)

    t = 100.0
    # Hold scrolling for 1.5s -> no trigger
    assert tracker.update_phone_posture("PHONE_SCROLLING", timestamp=t) is None
    assert tracker.update_phone_posture("PHONE_SCROLLING", timestamp=t + 1.5) is None

    # Reaches 2.0s -> triggers PHONE_SCROLLING
    assert tracker.update_phone_posture("PHONE_SCROLLING", timestamp=t + 2.0) == "PHONE_SCROLLING"

    # Continues holding in same streak -> no re-trigger
    assert tracker.update_phone_posture("PHONE_SCROLLING", timestamp=t + 3.0) is None

    # Switch to gaming at t=105.0: hold for 2.0s (t=107.0)
    # Different posture event has its own cooldown!
    assert tracker.update_phone_posture("PHONE_GAMING", timestamp=t + 5.0) is None
    assert tracker.update_phone_posture("PHONE_GAMING", timestamp=t + 6.0) is None
    assert tracker.update_phone_posture("PHONE_GAMING", timestamp=t + 7.1) == "PHONE_GAMING"
