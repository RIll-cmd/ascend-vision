"""Behavior tests for deterministic hand gesture controls."""
import pytest


def _hand(*extended, handedness="Right"):
    """Synthetic upright MediaPipe landmarks with named extended fingers."""
    points = [(0.50, 0.80, 0.0)] * 21
    points[1], points[2], points[3] = (0.46, 0.72, 0.0), (0.42, 0.68, 0.0), (0.40, 0.65, 0.0)
    thumb_tip = 0.28 if handedness == "Right" else 0.72
    points[4] = (thumb_tip if "thumb" in extended else 0.44, 0.62 if "thumb" in extended else 0.73, 0.0)
    for name, mcp, pip, dip, tip, x in (
        ("index", 5, 6, 7, 8, 0.40),
        ("middle", 9, 10, 11, 12, 0.48),
        ("ring", 13, 14, 15, 16, 0.56),
        ("pinky", 17, 18, 19, 20, 0.64),
    ):
        points[mcp], points[pip], points[dip] = (x, 0.70, 0.0), (x, 0.58, 0.0), (x, 0.52, 0.0)
        points[tip] = (x, 0.35 if name in extended else 0.68, 0.0)
    return points


def test_fist_is_detected_as_zero_fingers():
    from gesture_controls import count_fingers

    # Wrist, then a closed hand: every fingertip is below its PIP joint.
    fist = [
        (0.50, 0.80, 0.0), (0.45, 0.70, 0.0), (0.42, 0.65, 0.0), (0.44, 0.69, 0.0), (0.46, 0.73, 0.0),
        (0.46, 0.66, 0.0), (0.45, 0.60, 0.0), (0.45, 0.64, 0.0), (0.45, 0.69, 0.0),
        (0.50, 0.63, 0.0), (0.50, 0.57, 0.0), (0.50, 0.62, 0.0), (0.50, 0.68, 0.0),
        (0.54, 0.66, 0.0), (0.55, 0.61, 0.0), (0.55, 0.65, 0.0), (0.55, 0.70, 0.0),
        (0.58, 0.70, 0.0), (0.59, 0.67, 0.0), (0.59, 0.70, 0.0), (0.59, 0.74, 0.0),
    ]

    assert count_fingers(fist, handedness="Right") == 0


@pytest.mark.parametrize(
    ("fingers", "expected"),
    [
        (("index",), 1),
        (("index", "middle"), 2),
        (("index", "middle", "ring"), 3),
        (("index", "middle", "ring", "pinky"), 4),
        (("thumb", "index", "middle", "ring", "pinky"), 5),
    ],
)
def test_deterministic_finger_counts(fingers, expected):
    from gesture_controls import count_fingers

    assert count_fingers(_hand(*fingers), handedness="Right") == expected


def test_thumb_count_is_correct_for_left_hand_orientation():
    from gesture_controls import count_fingers

    assert count_fingers(_hand("thumb", "index", "middle", "ring", "pinky", handedness="Left"), handedness="Left") == 5


def test_transient_gesture_does_not_trigger():
    from gesture_controls import GestureRecognizer

    recognizer = GestureRecognizer(stable_seconds=0.9, cooldown_seconds=1.5)
    assert recognizer.update(2, 0.0) is None
    assert recognizer.update(2, 0.89) is None


def test_stable_gesture_triggers_once_and_a_held_hand_does_not_repeat():
    from gesture_controls import GestureRecognizer

    recognizer = GestureRecognizer(stable_seconds=0.9, cooldown_seconds=1.5)
    assert recognizer.update(3, 0.0) is None
    assert recognizer.update(3, 0.9) == 3
    assert recognizer.update(3, 2.5) is None
    assert recognizer.update(None, 2.6) is None
    assert recognizer.update(3, 2.7) is None
    assert recognizer.update(3, 3.6) == 3


def test_fist_mutes_and_only_open_palm_unmutes_while_muted():
    from gesture_controls import GestureAction, GestureController

    controller = GestureController()
    assert controller.handle(0) is GestureAction.MUTE
    assert controller.muted is True
    assert controller.handle(1) is None
    assert controller.handle(4) is None
    assert controller.handle(5) is GestureAction.UNMUTE
    assert controller.muted is False


def test_open_palm_unmutes_an_existing_feedback_mute_state():
    from gesture_controls import GestureAction, GestureController

    controller = GestureController()
    assert controller.handle(2, externally_muted=True) is None
    assert controller.handle(5, externally_muted=True) is GestureAction.UNMUTE


def test_open_palm_stops_and_cancels_when_not_muted():
    from gesture_controls import GestureAction, GestureController, GestureMode

    controller = GestureController()
    controller.handle(2)
    assert controller.mode is GestureMode.AUTOMATION
    assert controller.handle(5) is GestureAction.STOP_CANCEL
    assert controller.mode is GestureMode.IDLE


@pytest.mark.parametrize(
    ("count", "mode"),
    [(1, "chat"), (2, "automation"), (3, "missions"), (4, "habits")],
)
def test_gesture_mode_routes_exactly_one_following_utterance(count, mode):
    from gesture_controls import GestureController

    controller = GestureController()
    controller.handle(count)
    assert controller.consume_mode() == mode
    assert controller.consume_mode() is None


def test_explicit_gesture_modes_route_to_only_the_selected_flow():
    from gesture_controls import GestureModeRouter

    calls = []
    router = GestureModeRouter(
        chat=lambda text: calls.append(("chat", text)),
        automation=lambda text: calls.append(("automation", text)),
        missions=lambda text: calls.append(("missions", text)),
        habits=lambda text: calls.append(("habits", text)),
    )

    for mode in ("chat", "automation", "missions", "habits"):
        assert router.route(mode, "next request") is True
    assert calls == [
        ("chat", "next request"),
        ("automation", "next request"),
        ("missions", "next request"),
        ("habits", "next request"),
    ]


def test_camera_overlay_labels_live_gesture_state():
    from gesture_controls import GestureController, GestureRecognizer, gesture_overlay_lines

    controller = GestureController()
    controller.handle(3)
    recognizer = GestureRecognizer(stable_seconds=0.9)
    recognizer.update(3, 10.0)

    assert gesture_overlay_lines(
        finger_count=3,
        handedness="Left",
        recognizer=recognizer,
        controller=controller,
        feedback_muted=False,
        now=10.5,
        last_action="Missions mode selected",
        core_status="CORE: CONNECTED",
        vision_status="VISION: CONNECTED",
    ) == [
        "GESTURE CONTROLS",
        "VOICE: ACTIVE | CORE: CONNECTED",
        "VISION: CONNECTED",
        "HAND: Left | FINGERS: 3",
        "HOLD: ######---- 0.5 / 0.9s",
        "MODE: MISSIONS",
        "LAST: Missions mode selected",
    ]


def test_core_indicator_distinguishes_disabled_connecting_connected_and_unavailable():
    from gesture_controls import core_status_indicator

    assert core_status_indicator(configured=False, state=None) == ("CORE: DISABLED", "muted")
    assert core_status_indicator(configured=True, state=None) == ("CORE: CONNECTING...", "warning")
    assert core_status_indicator(configured=True, state="ASCEND_CONNECTED") == ("CORE: CONNECTED", "good")
    assert core_status_indicator(configured=True, state="ASCEND_OFFLINE") == ("CORE: UNAVAILABLE", "bad")


def test_successful_authenticated_vision_heartbeat_proves_core_is_reachable():
    from gesture_controls import effective_core_connection_state

    assert effective_core_connection_state("ASCEND_AUTH_ERROR", "ASCEND_CONNECTED") == "ASCEND_CONNECTED"
    assert effective_core_connection_state("ASCEND_OFFLINE", None) == "ASCEND_OFFLINE"


def test_vision_presence_indicator_distinguishes_connection_and_auth_states():
    from gesture_controls import vision_presence_indicator

    assert vision_presence_indicator(configured=False, state=None) == ("VISION: DISABLED", "muted")
    assert vision_presence_indicator(configured=True, state=None) == ("VISION: CONNECTING...", "warning")
    assert vision_presence_indicator(configured=True, state="CONNECTED") == ("VISION: CONNECTED", "good")
    assert vision_presence_indicator(configured=True, state="ASCEND_AUTH_ERROR") == ("VISION: RE-AUTH REQUIRED", "bad")
    assert vision_presence_indicator(configured=True, state="ASCEND_OFFLINE") == ("VISION: OFFLINE", "bad")
