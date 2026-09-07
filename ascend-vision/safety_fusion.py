"""Centralized Safety Fusion Manager.

Unifies inputs from:
1. Phone Detection & Posture Analysis (HoldMachine)
2. Eye Closure / Drowsiness Detection (DrowsinessMachine)
3. Yawn / Fatigue Detection (YawnMachine)

Coordinates prioritized alert dispatching, multi-event lifecycle tracking,
composite fatigue scoring, and unified status reporting.
"""
from dataclasses import dataclass, field
from datetime import datetime

from drowsiness_detector import DrowsinessMachine, DrowsinessUpdate
from posture_detector import PostureMachine, PostureUpdate
from state_machine import HoldMachine, HoldUpdate
from yawn_detector import YawnMachine, YawnUpdate


@dataclass(frozen=True)
class SafetyAlert:
    event_type: str  # 'drowsiness_microsleep', 'yawn', 'phone_held', 'slouch'
    started_at: datetime
    posture: str  # 'call', 'texting', 'active_hold', 'none'
    alert_allowed: bool
    details: dict
    id: int = 0


@dataclass(frozen=True)
class SafetyActiveEvent:
    event_type: str
    id: int
    duration_seconds: float
    posture: str = 'none'


@dataclass(frozen=True)
class SafetyEndedEvent:
    event_type: str
    id: int
    duration_seconds: float
    reason: str
    posture: str = 'none'


@dataclass(frozen=True)
class FusionStatus:
    hold: HoldUpdate
    drowsiness: DrowsinessUpdate
    yawn: YawnUpdate
    posture_status: PostureUpdate | None = None
    primary_alert: SafetyAlert | None = None  # Prioritized alert to voice if any triggered this frame
    started_events: list[SafetyAlert] = field(default_factory=list)
    active_events: list[SafetyActiveEvent] = field(default_factory=list)
    ended_events: list[SafetyEndedEvent] = field(default_factory=list)
    fatigue_score: float = 0.0  # Normalized 0.0 (alert) to 1.0 (severe drowsiness)
    fatigue_level: str = 'alert'  # 'alert', 'mild_fatigue', 'moderate_fatigue', 'critical_drowsy'


class SafetyFusionManager:
    """Coordinates phone hold, eye closure drowsiness, yawning, and posture state machines."""

    def __init__(self, hold_machine: HoldMachine, drowsiness_machine: DrowsinessMachine,
                 yawn_machine: YawnMachine, posture_machine: PostureMachine | None = None):
        self.hold_machine = hold_machine
        self.drowsiness_machine = drowsiness_machine
        self.yawn_machine = yawn_machine
        self.posture_machine = posture_machine

    def update(self, box, hands, face_landmarks, now: float, captured_at: datetime) -> FusionStatus:
        # 1. Update phone hold machine
        hold_update = self.hold_machine.update(box, hands, now, captured_at, face_landmarks=face_landmarks)

        # 2. Update drowsiness machine
        drowsiness_update = self.drowsiness_machine.update(face_landmarks, now, captured_at)

        # 3. Update yawn machine
        yawn_update = self.yawn_machine.update(face_landmarks, now, captured_at)

        # 4. Update posture machine (slouch / forward head posture)
        posture_update = self.posture_machine.update(face_landmarks, now, captured_at) if self.posture_machine is not None else None

        # 5. Collect started events across all machines
        started_events: list[SafetyAlert] = []
        if drowsiness_update.started is not None:
            started_events.append(SafetyAlert(
                event_type='drowsiness_microsleep',
                started_at=drowsiness_update.started.started_at,
                posture='none',
                alert_allowed=drowsiness_update.started.alert_allowed,
                details={'ear': drowsiness_update.ear, 'consecutive_frames': drowsiness_update.consecutive_frames},
                id=drowsiness_update.started.id
            ))
        if yawn_update.started is not None:
            started_events.append(SafetyAlert(
                event_type='yawn',
                started_at=yawn_update.started.started_at,
                posture='none',
                alert_allowed=yawn_update.started.alert_allowed,
                details={'mar': yawn_update.mar, 'yawn_count': yawn_update.yawn_count},
                id=yawn_update.started.id
            ))
        if hold_update.started is not None:
            started_events.append(SafetyAlert(
                event_type='phone_held',
                started_at=hold_update.started.started_at,
                posture=hold_update.started.posture,
                alert_allowed=hold_update.started.alert_allowed,
                details={'confidence': hold_update.started.confidence, 'distance_px': hold_update.distance_px},
                id=hold_update.started.id
            ))
        if posture_update is not None and posture_update.started is not None:
            started_events.append(SafetyAlert(
                event_type='slouch',
                started_at=posture_update.started.started_at,
                posture='none',
                alert_allowed=posture_update.started.alert_allowed,
                details={'slouch_score': posture_update.started.slouch_score},
                id=posture_update.started.id
            ))

        # 6. Collect continuous active events
        active_events: list[SafetyActiveEvent] = []
        if drowsiness_update.active is not None:
            active_events.append(SafetyActiveEvent(
                event_type='drowsiness_microsleep',
                id=drowsiness_update.active.id,
                duration_seconds=drowsiness_update.active.duration_seconds
            ))
        if yawn_update.active is not None:
            active_events.append(SafetyActiveEvent(
                event_type='yawn',
                id=yawn_update.active.id,
                duration_seconds=yawn_update.active.duration_seconds
            ))
        if hold_update.active is not None:
            active_events.append(SafetyActiveEvent(
                event_type='phone_held',
                id=hold_update.active.id,
                duration_seconds=hold_update.active.duration_seconds,
                posture=hold_update.posture
            ))
        if posture_update is not None and posture_update.active is not None:
            active_events.append(SafetyActiveEvent(
                event_type='slouch',
                id=posture_update.active.id,
                duration_seconds=posture_update.active.duration_seconds
            ))

        # 7. Collect ended events
        ended_events: list[SafetyEndedEvent] = []
        if drowsiness_update.ended is not None:
            r = getattr(drowsiness_update.ended, 'reason', getattr(drowsiness_update.ended, 'end_reason', 'completed')) or 'completed'
            ended_events.append(SafetyEndedEvent(
                event_type='drowsiness_microsleep',
                id=drowsiness_update.ended.id,
                duration_seconds=drowsiness_update.ended.duration_seconds,
                reason=r
            ))
        if yawn_update.ended is not None:
            r = getattr(yawn_update.ended, 'reason', getattr(yawn_update.ended, 'end_reason', 'completed')) or 'completed'
            ended_events.append(SafetyEndedEvent(
                event_type='yawn',
                id=yawn_update.ended.id,
                duration_seconds=yawn_update.ended.duration_seconds,
                reason=r
            ))
        if hold_update.ended is not None:
            r = getattr(hold_update.ended, 'reason', getattr(hold_update.ended, 'end_reason', 'completed')) or 'completed'
            ended_events.append(SafetyEndedEvent(
                event_type='phone_held',
                id=hold_update.ended.id,
                duration_seconds=hold_update.ended.duration_seconds,
                reason=r,
                posture=getattr(hold_update.ended, 'posture', 'none')
            ))
        if posture_update is not None and posture_update.ended is not None:
            r = getattr(posture_update.ended, 'reason', getattr(posture_update.ended, 'end_reason', 'completed')) or 'completed'
            ended_events.append(SafetyEndedEvent(
                event_type='slouch',
                id=posture_update.ended.id,
                duration_seconds=posture_update.ended.duration_seconds,
                reason=r
            ))

        # 8. Determine primary alert by strict safety priority:
        # 1st: Drowsiness / microsleep (critical safety risk)
        # 2nd: Yawning (fatigue progression)
        # 3rd: Phone holding (distraction)
        # 4th: Slouch / poor posture (habit correction)
        primary_alert = None
        for alert in started_events:
            if alert.event_type == 'drowsiness_microsleep' and alert.alert_allowed:
                primary_alert = alert
                break

        if primary_alert is None:
            for alert in started_events:
                if alert.event_type == 'yawn' and alert.alert_allowed:
                    primary_alert = alert
                    break

        if primary_alert is None:
            for alert in started_events:
                if alert.event_type == 'phone_held' and alert.alert_allowed:
                    primary_alert = alert
                    break

        if primary_alert is None:
            for alert in started_events:
                if alert.event_type == 'slouch' and alert.alert_allowed:
                    primary_alert = alert
                    break

        # 9. Compute composite fatigue score and level
        fatigue_score, fatigue_level = self._compute_fatigue(drowsiness_update, yawn_update)

        return FusionStatus(
            hold=hold_update,
            drowsiness=drowsiness_update,
            yawn=yawn_update,
            posture_status=posture_update,
            primary_alert=primary_alert,
            started_events=started_events,
            active_events=active_events,
            ended_events=ended_events,
            fatigue_score=fatigue_score,
            fatigue_level=fatigue_level,
        )

    def _compute_fatigue(self, drowsiness_update: DrowsinessUpdate, yawn_update: YawnUpdate) -> tuple[float, str]:
        """Compute composite fatigue score [0.0, 1.0] and qualitative level."""
        if drowsiness_update.state == 'drowsy' or drowsiness_update.started is not None:
            return 1.0, 'critical_drowsy'

        if yawn_update.state == 'yawning' or yawn_update.started is not None:
            return 0.75, 'moderate_fatigue'

        if drowsiness_update.state == 'closing':
            consec = drowsiness_update.consecutive_frames
            threshold = getattr(getattr(self.drowsiness_machine, 'config', None), 'closed_consec_frames', 15)
            ratio = min(1.0, consec / max(1, threshold))
            score = round(0.3 + 0.35 * ratio, 2)
            return score, 'mild_fatigue' if score < 0.6 else 'moderate_fatigue'

        if yawn_update.state == 'opening':
            return 0.45, 'mild_fatigue'

        if getattr(yawn_update, 'yawn_count', 0) >= 3:
            return 0.35, 'mild_fatigue'

        return 0.0, 'alert'

    def finish(self, reason: str = 'shutdown'):
        res = {
            'hold': self.hold_machine.finish(reason),
            'drowsiness': self.drowsiness_machine.finish(reason),
            'yawn': self.yawn_machine.finish(reason),
        }
        if self.posture_machine is not None:
            res['posture'] = self.posture_machine.finish(reason)
        return res
