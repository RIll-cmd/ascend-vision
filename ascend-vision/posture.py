"""Phone posture classification and trajectory smoothing.

Provides contextual posture classification (e.g. call-to-head, lap texting, active hold)
and relative motion trajectory analysis between phone and hands to eliminate
transient reaching false positives.
"""
from collections import deque
from dataclasses import dataclass
import math

from detector import PhoneBox


def get_face_bbox(face_landmarks) -> tuple[float, float, float, float] | None:
    """Compute (x_min, y_min, x_max, y_max) bounding box from face landmarks."""
    if not face_landmarks or len(face_landmarks) < 10:
        return None
    xs = [pt[0] for pt in face_landmarks if math.isfinite(pt[0])]
    ys = [pt[1] for pt in face_landmarks if math.isfinite(pt[1])]
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def is_near_ear(box, face_landmarks, call_proximity_px: float = 120.0) -> bool:
    """Check if the phone bounding box is adjacent or overlapping the ear/head region."""
    if box is None or not face_landmarks:
        return False
    face_bbox = get_face_bbox(face_landmarks)
    if face_bbox is None:
        return False
    bx1, by1, bx2, by2 = box.xyxy if hasattr(box, 'xyxy') else box
    phone_cx = (bx1 + bx2) / 2.0
    phone_cy = (by1 + by2) / 2.0
    fx1, fy1, fx2, fy2 = face_bbox
    face_cx = (fx1 + fx2) / 2.0

    dx_head = min(abs(bx2 - fx1), abs(bx1 - fx2), abs(phone_cx - face_cx))
    in_vertical_span = (fy1 - 50.0) <= phone_cy <= (fy2 + 80.0)
    near_head_sides = dx_head <= call_proximity_px
    return bool(in_vertical_span and near_head_sides)


def is_in_hand(box, hands, proximity_px: float = 100.0) -> bool:
    """Check if phone box is in hand (proximity to hand or wrist landmarks)."""
    if box is None:
        return False
    if hands is None:
        return True
    if not hands:
        return False
    bx1, by1, bx2, by2 = box.xyxy if hasattr(box, 'xyxy') else box
    for hand in hands:
        for pt in hand:
            if len(pt) < 2:
                continue
            hx, hy = pt[0], pt[1]
            if not (math.isfinite(hx) and math.isfinite(hy)):
                continue
            dx = max(0.0, max(bx1 - hx, hx - bx2))
            dy = max(0.0, max(by1 - hy, hy - by2))
            if math.hypot(dx, dy) <= proximity_px:
                return True
    return False


def classify_phone_orientation(
    box,
    hands=None,
    face_landmarks=None,
    call_proximity_px: float = 120.0,
    hand_proximity_px: float = 100.0,
) -> str:
    """Classifies phone interaction posture and orientation.

    Returns:
        'PHONE_CALL': Phone is near ear/head region.
        'PHONE_SCROLLING': Phone in hand with vertical aspect ratio (height / width >= 1.25).
        'PHONE_GAMING': Phone in hand with horizontal aspect ratio (height / width <= 0.80).
        'PHONE_USE': Phone in hand with ambiguous/tilted aspect ratio.
        'NONE': No phone or not in hand.
    """
    if box is None:
        return "NONE"

    bx1, by1, bx2, by2 = box.xyxy if hasattr(box, 'xyxy') else box
    width = max(0.0, float(bx2 - bx1))
    height = max(0.0, float(by2 - by1))
    if width <= 0 or height <= 0:
        return "NONE"

    aspect_ratio = height / max(width, 1.0)

    # 1. Calling: adjacent or overlapping ear/head region
    if is_near_ear(box, face_landmarks, call_proximity_px):
        return "PHONE_CALL"

    # 2. In-Hand usage
    if is_in_hand(box, hands, hand_proximity_px):
        if aspect_ratio >= 1.25:
            return "PHONE_SCROLLING"
        elif aspect_ratio <= 0.80:
            return "PHONE_GAMING"
        else:
            return "PHONE_USE"

    return "NONE"


def classify_phone_posture(box: PhoneBox | None, hands, face_landmarks, call_proximity_px: float = 120.0) -> str:
    """Classify the user's phone usage posture based on face and hand spatial context.

    Returns:
        'none': No phone or no hand interaction.
        'call': Phone is held near the head/ear region.
        'texting': Phone is held lower in front of torso/lap with hand interaction.
        'active_hold': General confirmed phone-in-hand hold.
    """
    if box is None or not hands:
        return 'none'

    bx1, by1, bx2, by2 = box.xyxy
    phone_cx = (bx1 + bx2) / 2.0
    phone_cy = (by1 + by2) / 2.0

    face_bbox = get_face_bbox(face_landmarks)
    if face_bbox is not None:
        fx1, fy1, fx2, fy2 = face_bbox
        face_cx = (fx1 + fx2) / 2.0
        face_cy = (fy1 + fy2) / 2.0

        # Check call-to-head posture: phone near horizontal sides of head (ear zones)
        # and vertical span between forehead and jaw
        dx_head = min(abs(bx2 - fx1), abs(bx1 - fx2), abs(phone_cx - face_cx))
        in_vertical_span = (fy1 - 50.0) <= phone_cy <= (fy2 + 80.0)
        near_head_sides = dx_head <= call_proximity_px

        if in_vertical_span and near_head_sides:
            return 'call'

        # Check lap texting posture: phone well below jawline
        if by1 > (fy2 + 40.0):
            return 'texting'

    return 'active_hold'



@dataclass(frozen=True)
class MotionPoint:
    time: float
    phone_pos: tuple[float, float] | None
    hand_pos: tuple[float, float] | None


class TrajectorySmoother:
    """Rolling multi-frame trajectory smoother and movement consistency tracker.

    Differentiates a hand reaching past a stationary desk phone (transient false positive)
    from a hand actively holding/carrying a phone in motion.
    """

    def __init__(self, max_frames: int = 5):
        self.max_frames = max_frames
        self.history: deque[MotionPoint] = deque(maxlen=max_frames)

    def update(self, now: float, box: PhoneBox | None, hands) -> float:
        """Update trajectory buffer and return relative motion stability score [0.0 to 1.0].

        1.0 indicates phone and hand are either moving together in tandem or stably held.
        Lower values indicate high divergent relative movement (e.g. hand sweeping past static phone).
        """
        phone_pos = None
        if box is not None:
            x1, y1, x2, y2 = box.xyxy
            phone_pos = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

        hand_pos = None
        if hands:
            # Use wrist (index 0) or palm/middle base (index 9) of first active hand
            first_hand = hands[0]
            if len(first_hand) >= 10:
                hx, hy = first_hand[0][:2]
                if math.isfinite(hx) and math.isfinite(hy):
                    hand_pos = (hx, hy)

        self.history.append(MotionPoint(now, phone_pos, hand_pos))

        if len(self.history) < 2:
            return 1.0

        p_prev = self.history[-2]
        p_curr = self.history[-1]

        if p_prev.phone_pos is None or p_curr.phone_pos is None:
            return 1.0
        if p_prev.hand_pos is None or p_curr.hand_pos is None:
            return 1.0

        dt = max(1e-4, p_curr.time - p_prev.time)

        # Phone displacement and Hand displacement
        d_phone = math.hypot(p_curr.phone_pos[0] - p_prev.phone_pos[0],
                             p_curr.phone_pos[1] - p_prev.phone_pos[1])
        d_hand = math.hypot(p_curr.hand_pos[0] - p_prev.hand_pos[0],
                            p_curr.hand_pos[1] - p_prev.hand_pos[1])

        v_phone = d_phone / dt
        v_hand = d_hand / dt

        # If hand is moving at high speed (> 400 px/s) while phone is virtually stationary (< 50 px/s),
        # this is typical transient reach-over behavior
        if v_hand > 400.0 and v_phone < 50.0:
            return 0.1

        # Relative velocity difference
        rel_diff = abs(v_hand - v_phone)
        stability = max(0.0, 1.0 - (rel_diff / 500.0))
        return stability

    def reset(self):
        self.history.clear()
