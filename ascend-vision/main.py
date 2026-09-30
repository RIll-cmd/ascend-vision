"""Phase 4: local hold logging and asynchronous focus-only roast feedback."""
import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import logging
import math
import os
from pathlib import Path
import signal
import sqlite3
import threading
import time
from contextlib import ExitStack

import cv2

from capture import CameraCapture, CaptureError
from assistant.context import build_conversation_context
from assistant.hub_status import parse_status_intent
from assistant.memory import MemoryStore, UnavailableMemoryStore
from assistant.service import AssistantService
from assistant.tool_runtime import ToolRuntime, ToolSpec
from config import Config, load_config, number
from detector import PhoneDetector, download_model
from hands import HandTracker, download_hand_model
from face_mesh import FaceMeshTracker, download_face_model
from expression_tracker import ExpressionTracker
from drowsiness_detector import DrowsinessMachine, LEFT_EYE_LANDMARKS, RIGHT_EYE_LANDMARKS
from yawn_detector import YawnMachine, MOUTH_LANDMARKS
from posture_detector import PostureMachine
from posture import classify_phone_orientation
from state_machine import HoldMachine, HOLD_LANDMARKS
from safety_fusion import SafetyFusionManager
from db import Database
from session_manager import SessionManager
from controls import DesktopControls
from feedback import FeedbackService, RoastContext, ConversationContext
from integrations.status_shelf import ShelfSnapshot, StatusShelfReader
from gesture_controls import (GestureController, GestureRecognizer,
                              core_status_indicator, count_fingers, hand_overlaps_phone,
                              effective_core_connection_state, gesture_overlay_lines,
                              vision_presence_indicator)
from voice_listener import VoiceCommandListener, VoiceCommand

LOG = logging.getLogger('phone_watch')


def stop_local_browser_tasks(browser_task_client, task_ids, session_key) -> int:
    """Request owner-session-scoped cancellation for every tracked local task."""
    if browser_task_client is None:
        return 0
    stopped = 0
    for task_id in task_ids:
        try:
            browser_task_client.control(task_id, 'stop', session_key)
            stopped += 1
        except Exception as exc:
            LOG.info('Browser task stop request failed (%s)', type(exc).__name__)
    return stopped

WINDOW = 'Phone Watch - Space: focus toggle | Q / Esc: quit'
VISION_VERSION = '1.0.0'



def run(config: Config, *, duration=None, detector=None, capture=None, hand_tracker=None,
        face_tracker=None, voice_listener=None, fairy_ui=False, assistant_service=None):
    if duration is not None:
        number('duration', duration, .01)

    if detector is None:
        detector = PhoneDetector(config.detector)
        LOG.info('Warming up %s on %s', config.detector.model.name, config.detector.device)
        detector.warmup(config.camera.width, config.camera.height)
    capture = capture if capture is not None else CameraCapture(config.camera)
    start = time.perf_counter()
    next_report = start + config.runtime.stats_interval_seconds
    processed = detections = skipped = dropped = consumed = sequence = 0
    inference_seconds = 0.
    window_created = False
    hold_machine = HoldMachine(config.hold)
    drowsiness_machine = DrowsinessMachine(config.drowsiness)
    yawn_machine = YawnMachine(config.yawn)
    posture_machine = PostureMachine(config.posture)
    fusion = SafetyFusionManager(hold_machine, drowsiness_machine, yawn_machine, posture_machine)
    confirmed = 0
    hand_frames = 0
    face_frames = 0
    finish_reason = 'shutdown'
    manager = None
    controls = None
    feedback = None
    context_runtime = None
    context_collector = None
    activity_history_store = None
    activity_summary_provider = None
    browser_voice_notifier = None
    browser_task_client = None
    browser_task_lock = threading.Lock()
    active_local_browser_tasks: dict[tuple[str, str, str], list[str]] = {}
    mission_reader = None
    activity_summary_recorder = None
    last_activity_sample = None
    chat_queue = None
    local_conversation = None
    face_tracker_available = face_tracker is not None
    resources = ExitStack()
    focus_ui_bridge = None
    if fairy_ui:
        from focus_ui import FocusUI
        focus_ui_bridge = FocusUI()
        resources.callback(focus_ui_bridge.close)
        try:
            focus_ui_bridge.start(open_browser=not config.runtime.preview)
        except TypeError:
            focus_ui_bridge.start()

    def collect_feedback():
        for result in feedback.drain():
            if result.error:
                LOG.warning('Feedback failed for event %d (%s); detection continues', result.event_id, result.error)
            if result.spoken:
                database.save_roast(result.event_id, result.text)
                LOG.info('ALERT_SPOKEN event_id=%d completed=%s text=%s', result.event_id, result.completed, result.text)

    def close_feedback():
        feedback.close()
        collect_feedback()

    def log_end(event, tag='HOLD_ENDED', event_type='phone_held'):
        if event is not None:
            if manager is not None and event_type in manager.active_event_types:
                active_entry = manager._active_by_type.get(event_type)
                if active_entry is not None and active_entry[0] == event.id:
                    manager.record_update(event, ended=True, event_type=event_type)
            LOG.info('%s id=%d started_at=%s ended_at=%s duration_s=%.3f reason=%s',
                     tag, event.id, event.started_at.isoformat(), event.ended_at.isoformat(),
                     event.duration_seconds, event.end_reason)



    def report():
        elapsed = max(time.perf_counter() - start, 1e-9)
        fps = processed / elapsed
        LOG.info('STATS elapsed_s=%.2f capture_fps=%.2f inference_fps=%.2f '
                 'processed=%d detections=%d dropped=%d skipped=%d mean_inference_ms=%.2f '
                 'hand_frames=%d face_frames=%d holds=%d blinks=%d yawns=%d active_duration_s=%.3f %s',
                 elapsed, capture.captured_count / elapsed, fps, processed, detections,
                 dropped, skipped, 1000 * inference_seconds / max(processed, 1),
                 hand_frames, face_frames, confirmed, drowsiness_machine.blink_count, yawn_machine.yawn_count,
                 hold_machine.active.duration_seconds if hold_machine.active else 0.,
                 'MEETS_TARGET' if fps >= config.runtime.target_fps else 'BELOW_TARGET')

    try:
        # Register callbacks before starting resources; all run even if persistence fails.
        resources.callback(capture.close)
        if hand_tracker is not None:
            resources.callback(hand_tracker.close)
        if hand_tracker is None:
            hand_tracker = HandTracker(config.hands)
            resources.callback(hand_tracker.close)
            hand_tracker.warmup(config.camera.width, config.camera.height)
        if face_tracker is not None:
            resources.callback(face_tracker.close)
        if face_tracker is None:
            try:
                face_tracker = FaceMeshTracker(config.face)
                face_tracker_available = True
                resources.callback(face_tracker.close)
                face_tracker.warmup(config.camera.width, config.camera.height)
            except ValueError:
                class DummyFaceTracker:
                    def detect(self, *args): return []
                    def close(self): pass
                face_tracker = DummyFaceTracker()
                face_tracker_available = False

        database = resources.enter_context(Database(config.storage.database,
                                                     timeout=config.storage.busy_timeout_seconds))
        manager = SessionManager(database, heartbeat_seconds=config.storage.heartbeat_seconds,
                                 update_seconds=config.storage.update_seconds)
        resources.callback(manager.close)
        manager.start(config.sessions.initial_mode)
        try:
            from assistant.activity_summary import ActivityHistoryStore, render_daily_summary
            activity_history_store = ActivityHistoryStore(
                Path(config.storage.database).parent / 'activity_history.db',
                timeout_seconds=config.storage.busy_timeout_seconds,
            )
            def activity_summary_provider():
                from zoneinfo import ZoneInfo
                now = datetime.now(timezone.utc)
                zone = ZoneInfo(config.dashboard.timezone)
                local_now = now.astimezone(zone)
                local_start = datetime.combine(local_now.date(), datetime.min.time(), tzinfo=zone)
                next_start = datetime.combine(local_now.date() + timedelta(days=1), datetime.min.time(), tzinfo=zone)
                mission_line = "Core mission completions could not be verified because authenticated Core access is unavailable."
                if mission_reader is not None:
                    try:
                        completions = mission_reader.read_completion_history(
                            local_start.astimezone(timezone.utc), next_start.astimezone(timezone.utc),
                        )
                        if completions:
                            names = "; ".join(
                                f"{item.name} ({item.completed_at.astimezone(zone).strftime('%H:%M')})"
                                for item in completions[:8]
                            )
                            mission_line = f"Core-confirmed mission completions today: {names}."
                            if len(completions) > 8:
                                mission_line += f" Plus {len(completions) - 8} more."
                        else:
                            mission_line = "Core-confirmed mission completions today: none."
                    except Exception:
                        mission_line = "Core mission completions could not be verified right now."
                summary = render_daily_summary(
                    activity_history_store, timezone_name=config.dashboard.timezone, now=now,
                )
                return f"{summary} {mission_line}"
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
            LOG.warning('Daily activity history unavailable (%s)', type(exc).__name__)
        desk_region_store = None
        if config.companion_context.enabled:
            from assistant.context_runtime import ContextRuntime
            from assistant.activity_summary import ActivitySummaryRecorder
            from integrations.context_collector import CompanionContextCollector
            from integrations.context_ipc import ContextPipeServer
            from integrations.desktop_activity import WindowsActivityAdapter
            from integrations.desk_presence import DeskRegionStore

            context_runtime = ContextRuntime(
                device_id=(os.getenv("ASCEND_DEVICE_ID", "ascend-vision-desktop").strip()
                           or "ascend-vision-desktop"),
                desk_calibration_available=config.runtime.preview,
            )
            desk_region_store = DeskRegionStore(
                Path(config.storage.database).parent / "desk_region.json",
            )
            context_runtime.set_desk_region(desk_region_store.load())
            resources.callback(context_runtime.clear)
            try:
                if activity_history_store is None:
                    raise RuntimeError('activity history store is unavailable')
                activity_summary_recorder = ActivitySummaryRecorder(
                    activity_history_store, timezone_name=config.dashboard.timezone,
                )
            except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
                LOG.warning('Daily activity aggregation unavailable (%s)', type(exc).__name__)
            context_collector = CompanionContextCollector(
                context_runtime,
                WindowsActivityAdapter(idle_after_seconds=config.companion_context.idle_after_seconds),
                desktop_poll_seconds=config.companion_context.desktop_poll_seconds,
                absence_dwell_seconds=config.companion_context.absence_dwell_seconds,
                return_dwell_seconds=config.companion_context.return_dwell_seconds,
            )
            try:
                context_server = ContextPipeServer(context_runtime)
                context_server.start()
                resources.callback(context_server.close)
                LOG.info("Current laptop context is available to the local dashboard.")
            except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
                LOG.warning("Local context dashboard IPC unavailable (%s)", type(exc).__name__)
            if config.companion_context.share_with_phone:
                from integrations.context_publisher import build_context_publisher

                context_publisher = build_context_publisher(
                    context_runtime, sharing_enabled=True,
                )
                if context_publisher is None:
                    raise RuntimeError('Enabled phone context sharing did not create a publisher')
                resources.callback(context_publisher.close)
                context_publisher.start()
                LOG.info('Redacted laptop context sharing is enabled for the paired phone account.')
        chat_session_started_at = time.perf_counter()
        controls = DesktopControls(config.sessions, manager)
        resources.callback(controls.close)
        controls.start()
        feedback = FeedbackService(config.feedback, config.hold.cooldown_seconds)
        resources.callback(close_feedback)
        feedback.set_session(manager.session_id if manager.mode == 'focus' else None)
        try:
            memory_store = MemoryStore(Path(config.storage.database).parent / 'assistant_memory.db')
            memory_store.discard_proposals()
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            LOG.warning('Assistant memory unavailable (%s); chat remains stateless', type(exc).__name__)
            memory_store = UnavailableMemoryStore()
        core_base_url = (os.getenv("ASCEND_CORE_BASE_URL") or os.getenv("ASCEND_BASE_URL", "http://localhost:8000")).strip()
        token_store = None
        context_store = None
        configured_character_id = os.getenv(config.ascend.character_id_env)
        if config.ascend.enabled:
            try:
                from integrations.vision_context import VisionContextStore
                from integrations.vision_token_store import VisionTokenStore
                token_store = VisionTokenStore()
                context_store = VisionContextStore()
            except Exception as exc:
                LOG.warning('Core read authorization unavailable (%s)', type(exc).__name__)
        if assistant_service is None:
            status_runtime = None
            status_credential = os.getenv("ASCEND_STATUS_READ_CREDENTIAL", "").strip()
            if config.ascend.enabled:
                try:
                    status_runtime = ToolRuntime()
                except ValueError as exc:
                    LOG.warning('Core read tools unavailable (%s)', type(exc).__name__)
                    status_runtime = None
                if status_runtime is not None:
                    if status_credential:
                        try:
                            reader = StatusShelfReader(
                                core_base_url, status_credential,
                                timeout_seconds=min(config.ascend.timeout_seconds, 3.0),
                            )
                            status_runtime.register(
                                ToolSpec("hub_status", 1, "read-only", frozenset(), ShelfSnapshot),
                                reader.read,
                            )
                        except ValueError as exc:
                            LOG.warning('Hub status tool unavailable (%s)', type(exc).__name__)
                    try:
                        from integrations.vision_context import resolve_character_id
                        from integrations.vision_query_reader import MissionSnapshot, VisionMissionReader

                        def mission_token():
                            token = token_store.load() if token_store is not None else None
                            return token.access_token if token is not None else None

                        def mission_character():
                            token = token_store.load() if token_store is not None else None
                            auth_context = context_store.load() if context_store is not None else None
                            return resolve_character_id(configured_character_id, auth_context, token)

                        mission_reader = VisionMissionReader(
                            core_base_url, mission_token, mission_character,
                            timeout_seconds=min(config.ascend.timeout_seconds, 3.0),
                        )
                        status_runtime.register(
                            ToolSpec("missions_summary", 1, "read-only", frozenset(), MissionSnapshot),
                            mission_reader.read_missions,
                        )
                    except ValueError as exc:
                        LOG.warning('Core mission reader unavailable (%s)', type(exc).__name__)
            screen_auditor = None
            try:
                from screen_auditor import ScreenAuditor
                screen_auditor = ScreenAuditor(
                    feedback_service=feedback,
                    interval_seconds=config.screen_audit.interval_seconds,
                )
                resources.callback(screen_auditor.stop)
                if config.screen_audit.enabled:
                    screen_auditor.start()
            except Exception as exc:
                LOG.warning('Screen inspection unavailable (%s)', type(exc).__name__)
            if config.browser_automation.enabled:
                try:
                    from browser.main import start_browser_broker
                    browser_task_client = start_browser_broker(
                        config.browser_automation, config.llm, resources,
                    )
                except Exception as exc:
                    LOG.warning('Local browser research unavailable (%s)', type(exc).__name__)
            if browser_task_client is not None:
                if config.browser_automation.remote_enabled:
                    try:
                        from integrations.browser_task_worker import build_browser_task_worker
                        browser_task_worker = build_browser_task_worker(
                            config.browser_automation, browser_task_client, llm_config=config.llm,
                        )
                        if browser_task_worker is not None:
                            resources.callback(browser_task_worker.stop)
                            browser_task_worker.start()
                            LOG.info('Remote browser task worker started; Core access is outbound-only')
                    except Exception as exc:
                        LOG.warning('Remote browser task worker unavailable (%s)', type(exc).__name__)
                try:
                    from integrations.browser_voice_notifier import BrowserVoiceCompletionNotifier

                    def voice_session_is_current(session_key):
                        return (not getattr(manager, 'quit_requested', False)
                                and session_key[1] == 'voice'
                                and session_key[2] == str(getattr(manager, 'session_id', 'default')))

                    browser_voice_notifier = BrowserVoiceCompletionNotifier(
                        browser_task_client, feedback, voice_session_is_current,
                        task_timeout_seconds=config.browser_automation.task_timeout_seconds,
                    )
                except Exception as exc:
                    LOG.warning('Browser voice result delivery unavailable (%s)', type(exc).__name__)

            def watch_local_browser_task(task_id, session_key):
                with browser_task_lock:
                    tasks = active_local_browser_tasks.setdefault(session_key, [])
                    tasks.append(task_id)
                    del tasks[:-8]
                if browser_voice_notifier is not None:
                    browser_voice_notifier.watch(task_id, session_key)

            assistant_service = AssistantService(
                config.feedback, config.llm, memory_store=memory_store,
                tool_runtime=status_runtime,
                context_provider=context_runtime,
                screen_inspector=screen_auditor,
                phone_context_sharing_enabled=config.companion_context.share_with_phone,
                daily_summary_provider=activity_summary_provider,
                browser_task_client=browser_task_client,
                browser_automation_config=config.browser_automation,
                browser_task_submitted=watch_local_browser_task,
            )
        bind_assistant = getattr(feedback, 'bind_assistant', None)
        if callable(bind_assistant):
            bind_assistant(assistant_service)
        feedback.start()
        if browser_voice_notifier is not None:
            browser_voice_notifier.start()
            resources.callback(browser_voice_notifier.stop)
        from integrations.notification_worker import build_notification_worker
        notification_worker = build_notification_worker()
        if notification_worker is not None:
            resources.callback(notification_worker.stop)
            notification_worker.start()
            LOG.info('Companion notification worker started; dispatching generic push alerts outbound-only')
        phone_chat_config = getattr(config, 'phone_chat', None)
        if phone_chat_config is not None and phone_chat_config.enabled:
            from assistant.phone_handler import PhoneMessageHandler
            from integrations.phone_worker import GeminiAudioTranscriber, build_phone_worker

            phone_owner_id = os.getenv(phone_chat_config.owner_id_env, '').strip()
            audio_transcriber = None
            if phone_chat_config.voice_upload_enabled:
                audio_transcriber = GeminiAudioTranscriber(
                    api_key=os.getenv(config.llm.gemini_api_key_env, '').strip().split(',')[0].strip(),
                    model=config.llm.gemini_model,
                )
            phone_handler = PhoneMessageHandler(assistant_service, owner_id=phone_owner_id,
                                                audio_transcriber=audio_transcriber)
            phone_worker = build_phone_worker(phone_chat_config, phone_handler)
            if phone_worker is None:
                raise RuntimeError('Enabled phone chat did not create its worker')
            resources.callback(phone_worker.stop)
            phone_worker.start()
            LOG.info('Phone chat worker started; Core queue access is outbound-only')
        expression_tracker = ExpressionTracker(cooldown_seconds=45.0)
        ascend_client = None
        ascend_character_id = None
        ascend_observations = None
        automation_proposals = None
        core_connection_state = {'health': None, 'vision': None, 'checked_at': None}
        vision_presence_state = {'value': None}
        core_connection_lock = threading.Lock()
        gesture_recognizer = GestureRecognizer()
        gesture_controller = GestureController()
        gesture_count = None
        gesture_handedness = None
        gesture_last_action = None
        gesture_last_action_at = float('-inf')
        active_panel = None
        active_panel_sequence = 0

        # Core integration initialization
        from vision_client.ascend_core_client import AscendCoreVisionClient
        from integrations.core_async_runner import CoreAsyncRunner
        from integrations.warning_state_machine import WarningFirstStateMachine, SensoryTriggerType
        from integrations.habit_voice_handler import HabitVoiceHandler
        from integrations.core_heartbeat import CoreHeartbeatWorker

        core_async_runner = CoreAsyncRunner()
        resources.callback(core_async_runner.close)

        if config.ascend.enabled:
            from integrations.ascend_client import AscendClient
            base_url = os.getenv(config.ascend.base_url_env, '').strip()
            configured_character_id = os.getenv(config.ascend.character_id_env)
            from integrations.vision_context import VisionContextStore, resolve_character_id
            from integrations.vision_token_store import VisionTokenStore
            token_store = None
            context_store = None
            synced_token = None
            try:
                token_store = VisionTokenStore()
                context_store = VisionContextStore()
                synced_token = token_store.load()
                synced_context = context_store.load()
            except Exception as exc:
                LOG.warning('Could not load synchronized Vision authorization (%s)', type(exc).__name__)
                synced_context = None
                synced_token = None
            ascend_character_id = resolve_character_id(configured_character_id, synced_context, synced_token)
            if base_url:
                ascend_client = AscendClient(base_url, os.getenv(config.ascend.api_token_env),
                                              timeout_seconds=config.ascend.timeout_seconds,
                                              status_credential=os.getenv("ASCEND_STATUS_CREDENTIAL"),
                                              health_path=config.ascend.health_path,
                                              command_path=config.ascend.command_path,
                                              event_path=config.ascend.event_path,
                                              token_store=token_store, context_store=context_store)
                if ascend_character_id:
                    from integrations.ascend_observations import AscendObservationDispatcher
                    ascend_observations = AscendObservationDispatcher(ascend_client, ascend_character_id)
                    resources.callback(ascend_observations.close)
                    from integrations.automation_llm import StructuredAutomationProposalGenerator
                    from integrations.automation_proposals import AutomationProposalService
                    from llm_router import get_router
                    automation_proposals = AutomationProposalService(
                        ascend_client, ascend_character_id, StructuredAutomationProposalGenerator(get_router(config.llm)))

        # Initialize official AscendCoreVisionClient
        core_bearer_token = (os.getenv("ASCEND_VISION_TOKEN") or os.getenv("ASCEND_API_TOKEN", "")).strip().strip('"')
        core_character_id = (os.getenv("ASCEND_CHARACTER_ID") or ascend_character_id or "").strip()
        core_device_id = (os.getenv("ASCEND_DEVICE_ID", "ascend-vision-desktop")).strip() or "ascend-vision-desktop"
        core_status_credential = (os.getenv("ASCEND_STATUS_CREDENTIAL") or "").strip()

        core_client = AscendCoreVisionClient(
            base_url=core_base_url,
            bearer_token=core_bearer_token,
            character_id=core_character_id,
            device_id=core_device_id,
            status_credential=core_status_credential,
        )

        warning_state_machine = WarningFirstStateMachine(
            core_client=core_client,
            feedback_service=feedback,
            async_runner=core_async_runner,
        )

        habit_voice_handler = HabitVoiceHandler(
            core_client=core_client,
            feedback_service=feedback,
            async_runner=core_async_runner,
        )

        # Start Presence Heartbeat (every 25-30s)
        if core_client is not None and core_character_id and core_status_credential:
            def on_core_heartbeat(result):
                state = result.get('status') if isinstance(result, dict) else None
                with core_connection_lock:
                    vision_presence_state['value'] = state or 'CONNECTED'
                    core_connection_state['vision'] = 'ASCEND_CONNECTED' if state != 'OFFLINE' else 'ASCEND_OFFLINE'
                    core_connection_state['checked_at'] = datetime.now(timezone.utc)

            core_heartbeat = CoreHeartbeatWorker(
                core_client,
                interval_seconds=25.0,
                callback=on_core_heartbeat,
                async_runner=core_async_runner,
            )
            core_heartbeat.start()
            resources.callback(core_heartbeat.close)
        elif ascend_client is not None and ascend_character_id:
            from integrations.vision_heartbeat import VisionHeartbeatWorker
            ascend_device_id = os.getenv(config.ascend.device_id_env, 'ascend-vision').strip() or 'ascend-vision'

            def on_vision_heartbeat(result):
                payload = getattr(result, 'payload', None)
                state = payload.get('status') if isinstance(payload, dict) else None
                with core_connection_lock:
                    vision_presence_state['value'] = state or result.state.value
                    core_connection_state['vision'] = result.state.value
                    core_connection_state['checked_at'] = datetime.now(timezone.utc)

            vision_heartbeat = VisionHeartbeatWorker(
                ascend_client,
                character_id=ascend_character_id,
                device_id=ascend_device_id,
                version=VISION_VERSION,
                interval_seconds=10.0,
                callback=on_vision_heartbeat,
            )
            vision_heartbeat.start()
            resources.callback(vision_heartbeat.close)

        if ascend_client is not None:
            core_monitor_stop = threading.Event()

            def monitor_core_connection():
                while not core_monitor_stop.is_set():
                    try:
                        result = ascend_client.get_vision_status(ascend_character_id)
                        state = result.state.value
                    except Exception:
                        state = 'ASCEND_OFFLINE'
                    with core_connection_lock:
                        core_connection_state['health'] = state
                        core_connection_state['checked_at'] = datetime.now(timezone.utc)
                    core_monitor_stop.wait(5.0)

            core_monitor = threading.Thread(target=monitor_core_connection,
                                            name='ascend-core-status', daemon=True)
            core_monitor.start()

            def stop_core_monitor():
                core_monitor_stop.set()
                core_monitor.join(1.0)

            resources.callback(stop_core_monitor)

        def send_ascend_command(text: str):
            from integrations.ascend_client import AscendConnectionState
            if ascend_client is None or not ascend_character_id:
                LOG.warning('ASCEND_CONFIG_ERROR: command not sent')
                feedback.speak_announcement('Ascend character configuration is missing.')
                return
            result = ascend_client.send_command(text, source='ascend_vision', character_id=ascend_character_id)
            with core_connection_lock:
                core_connection_state['health'] = result.state.value
                core_connection_state['checked_at'] = datetime.now(timezone.utc)
            LOG.info('Ascend command state: %s', result.state.value)
            if result.state is AscendConnectionState.CONNECTED:
                feedback.speak_announcement(result.message or 'Ascend completed your request.')
            else:
                feedback.speak_announcement('Ascend is unavailable right now.')

        def chat_context(text: str) -> ConversationContext:
            return build_conversation_context(
                text,
                session_id=manager.session_id,
                mode=manager.mode,
                database_path=config.storage.database,
                started_at=chat_session_started_at,
            )

        from integrations.chat_ipc import ChatIpcQueue
        from assistant.local_conversation import LocalConversation

        try:
            chat_queue = ChatIpcQueue(Path(config.storage.database).parent / 'chat_ipc.db')
            set_ai_status_publisher = getattr(assistant_service, 'set_ai_status_publisher', None)
            if callable(set_ai_status_publisher):
                set_ai_status_publisher(chat_queue.publish_ai_status)
            local_conversation = LocalConversation(
                chat_queue, assistant_service, chat_context,
                session_key=lambda: (
                    "local", "voice", str(getattr(manager, "session_id", "default")),
                ),
                max_words=config.voice_commands.max_reply_words,
            )
            local_conversation.start()
            resources.callback(local_conversation.close)
            if focus_ui_bridge is not None:
                focus_ui_bridge.bind_chat(chat_queue, local_conversation.runtime_session_id)
        except (OSError, sqlite3.Error, RuntimeError, TypeError, ValueError) as exc:
            LOG.warning('Shared local chat unavailable (%s); voice fallback remains enabled',
                        type(exc).__name__)
            chat_queue = None
            local_conversation = None

        last_voice_chat = [None, float('-inf')]
        voice_chat_lock = threading.Lock()
        def route_chat(text: str):
            if local_conversation is None:
                submit_chat = getattr(feedback, 'submit_chat', None)
                if submit_chat is not None:
                    submit_chat(
                        text, chat_context(text),
                        max_words=config.voice_commands.max_reply_words,
                        cooldown=config.voice_commands.chat_cooldown_seconds,
                        session_key=("local", "voice", str(getattr(manager, "session_id", "default"))),
                    )
                return
            now = time.monotonic()
            normalized = text.strip()
            with voice_chat_lock:
                if (normalized.casefold() == last_voice_chat[0]
                        and now - last_voice_chat[1] < config.voice_commands.chat_cooldown_seconds):
                    LOG.debug('Duplicate local voice chat ignored during cooldown')
                    return
                try:
                    local_conversation.enqueue(normalized, source='voice')
                    last_voice_chat[:] = [normalized.casefold(), now]
                except (ValueError, RuntimeError, sqlite3.Error):
                    LOG.exception('Could not queue local voice chat')

        def on_voice_command(cmd: VoiceCommand):
            if parse_status_intent(cmd.raw_text) is not None:
                route_chat(cmd.raw_text)
                return
            LOG.info("Executing voice command action: %s", cmd.action)
            if cmd.action == 'focus':
                manager.request('focus')
                feedback.speak_announcement("Focus mode enabled. Put your distractions away.")
            elif cmd.action == 'pause':
                manager.request('background')
                feedback.speak_announcement("Monitoring paused. Take a quick break.")
            elif cmd.action == 'music_toggle':
                from tools.spotify_tool import spotify_play_pause
                _, msg = spotify_play_pause()
                feedback.speak_announcement(msg)
            elif cmd.action == 'status':
                sid = manager.session_id
                counts = {}
                if sid is not None:
                    try:
                        counts = database.session_event_counts(sid)
                    except Exception as err:
                        LOG.debug('Database session_event_counts failed: %s', err)
                p = counts.get('phone_held', 0)
                d = counts.get('drowsiness_microsleep', 0)
                y = counts.get('yawn', 0)
                s = counts.get('slouch', 0)
                summary_text = (
                    f"Session status: {p} phone pickups, {d} microsleep events, "
                    f"{y} yawns, and {s} slouch events."
                )
                feedback.speak_announcement(summary_text)
            elif cmd.action == 'mute':
                feedback.mute(config.voice_commands.mute_duration_seconds)
            elif cmd.action == 'unmute':
                feedback.unmute()
                feedback.speak_announcement("Voice alerts unmuted.")

        def on_unmatched_speech(text: str):
            if parse_status_intent(text) is not None:
                route_chat(text)
                return
            if habit_voice_handler is not None and habit_voice_handler.handle_voice_utterance(text):
                return
            from integrations.ascend_routing import is_ascend_command
            if automation_proposals is not None:
                proposal_result = automation_proposals.handle_utterance(text)
                if proposal_result is not None:
                    feedback.speak_announcement(proposal_result.message)
                    return
            if is_ascend_command(text):
                send_ascend_command(text)
                return
            if not getattr(config, 'voice_commands', None) or not config.voice_commands.conversational_mode:
                return
            route_chat(text)

        if voice_listener is None and getattr(config, 'voice_commands', None) and config.voice_commands.enabled:
            voice_listener = VoiceCommandListener(
                config.voice_commands,
                callback=on_voice_command,
                unmatched_callback=on_unmatched_speech,
                is_speaking=getattr(feedback, 'is_speaking', None)
            )
        if voice_listener is not False and voice_listener is not None:
            resources.callback(voice_listener.close)
            voice_listener.start()

        companion_runtime = None
        shadow_mode = bool(getattr(config.companion_context, 'shadow_mode_enabled', False))
        if context_runtime is not None and (
                config.companion_context.proactive_enabled or shadow_mode):
            from assistant.companion_policy import CompanionPolicy, CompanionPreferences
            from assistant.companion_runtime import CompanionRuntime
            from assistant.intervention_delivery import InterventionDelivery
            agent_needs_input_provider = lambda: ()
            if config.companion_context.agent_needs_input_enabled:
                status_credential = os.getenv("ASCEND_STATUS_READ_CREDENTIAL", "").strip()
                if status_credential:
                    from integrations.status_shelf import AgentNeedsInputProvider
                    status_reader = StatusShelfReader(
                        (os.getenv("ASCEND_CORE_BASE_URL") or os.getenv("ASCEND_BASE_URL", "http://localhost:8000")).strip(),
                        status_credential,
                        timeout_seconds=min(getattr(config.ascend, "timeout_seconds", 3.0), 3.0),
                    )
                    agent_needs_input_provider = AgentNeedsInputProvider(status_reader)
                    agent_needs_input_provider.start()
                    resources.callback(agent_needs_input_provider.close)
                else:
                    LOG.warning('Agent needs-input rule is enabled but its dedicated shelf credential is unavailable.')

            def companion_preferences():
                recording = bool(getattr(voice_listener, 'is_recording', False))
                transcribing = bool(getattr(voice_listener, 'is_transcribing', False))
                session_id, session_mode, session_started_at = manager.session_snapshot()
                return CompanionPreferences(
                    enabled=(config.companion_context.proactive_enabled or shadow_mode),
                    mode=config.companion_context.mode,
                    break_suggestion_enabled=config.companion_context.break_suggestion_enabled,
                    desk_checkin_enabled=config.companion_context.desk_checkin_enabled,
                    agent_needs_input_enabled=config.companion_context.agent_needs_input_enabled,
                    focus_session_id=(str(session_id)
                                      if session_mode == 'focus' and session_id is not None
                                      else None),
                    focus_session_started_at=(session_started_at
                                              if session_mode == 'focus' else None),
                    local_speech_enabled=bool(getattr(feedback, 'enabled', False)),
                    user_speaking=recording or transcribing,
                    vision_speaking=bool(getattr(feedback, 'is_speaking', lambda: False)()),
                    speech_queue_busy=bool(getattr(feedback, 'has_pending_noncompanion_speech', lambda: False)()),
                )

            delivery = InterventionDelivery(
                receipt_path=Path(config.storage.database).parent / "companion_delivery.sqlite3",
            )
            resources.callback(delivery.close)
            from integrations.notification_publisher import build_notification_publisher
            notification_publisher = build_notification_publisher()
            if notification_publisher is not None:
                resources.callback(notification_publisher.close)
                notification_publisher.start()
            companion_runtime = CompanionRuntime(
                context_runtime,
                CompanionPolicy(
                    break_interval_seconds=config.companion_context.break_interval_minutes * 60,
                    desk_absence_seconds=config.companion_context.desk_absence_minutes * 60,
                ),
                delivery, companion_preferences, feedback,
                shadow_mode=shadow_mode,
                agent_needs_input_provider=agent_needs_input_provider,
                notification_publisher=notification_publisher,
            )
            LOG.info('Local companion rules running in %s mode (shadow=%s).',
                     config.companion_context.mode, shadow_mode)

        from integrations.chat_runtime import ChatRuntimeBridge

        try:
            if chat_queue is None or local_conversation is None:
                raise RuntimeError('Shared local chat is not initialized')
            def on_local_chat_reply(message, text):
                source = message.get('source')
                if (source == 'voice'
                        or (focus_ui_bridge is not None
                            and focus_ui_bridge.should_speak_replies())):
                    feedback.speak_announcement(text)

            chat_bridge = ChatRuntimeBridge(
                chat_queue, lambda _text: None,
                message_handler=local_conversation.handle_message,
                on_reply=on_local_chat_reply,
            )
            resources.callback(chat_bridge.stop)
            chat_bridge.start()
            resources.callback(local_conversation.stop_accepting)
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            LOG.warning('Dashboard chat unavailable (%s); monitoring continues', type(exc).__name__)
            if local_conversation is not None:
                try:
                    local_conversation.close()
                except (OSError, sqlite3.Error, RuntimeError):
                    LOG.warning('Could not close unavailable local chat runtime cleanly')

        # Both model initializations are excluded from throughput and duration.
        start = time.perf_counter()
        next_report = start + config.runtime.stats_interval_seconds
        capture.start()

        if config.runtime.preview:

            cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
            window_created = True
            calibration_drag = {
                "start": None, "end": None,
                "frame_width": config.camera.width, "frame_height": config.camera.height,
            }
            calibration_notice = {"text": ""}

            def on_preview_mouse(event, x, y, _flags, _parameter):
                if context_runtime is None or not context_runtime.desk_calibrating:
                    return
                frame_width = calibration_drag["frame_width"]
                frame_height = calibration_drag["frame_height"]
                image_width, image_height = frame_width, frame_height
                try:
                    image_rect = cv2.getWindowImageRect(WINDOW)
                    if image_rect[2] > 0 and image_rect[3] > 0:
                        image_width, image_height = image_rect[2], image_rect[3]
                except (AttributeError, cv2.error):
                    pass
                frame_x = max(0, min(frame_width, round(x * frame_width / image_width)))
                frame_y = max(0, min(frame_height, round(y * frame_height / image_height)))
                if event == cv2.EVENT_LBUTTONDOWN:
                    calibration_notice["text"] = ""
                    calibration_drag["start"] = (frame_x, frame_y)
                    calibration_drag["end"] = (frame_x, frame_y)
                elif event == cv2.EVENT_MOUSEMOVE and calibration_drag["start"] is not None:
                    calibration_drag["end"] = (frame_x, frame_y)
                elif event == cv2.EVENT_LBUTTONUP and calibration_drag["start"] is not None:
                    from assistant.context_runtime import DeskRegion
                    try:
                        region = DeskRegion.from_drag(
                            calibration_drag["start"], (frame_x, frame_y),
                            frame_width, frame_height,
                        )
                        desk_region_store.save(region)
                        context_runtime.set_desk_region(region)
                        calibration_notice["text"] = ""
                        LOG.info("Desk region calibration saved locally.")
                    except (OSError, ValueError) as exc:
                        calibration_notice["text"] = (
                            "Selection too small; drag a larger area." if isinstance(exc, ValueError)
                            else "Could not save the desk area; try again."
                        )
                        LOG.info("Desk region selection rejected (%s)", type(exc).__name__)
                    finally:
                        calibration_drag["start"] = None
                        calibration_drag["end"] = None

            if context_runtime is not None:
                cv2.setMouseCallback(WINDOW, on_preview_mouse)
            try:
                cv2.setWindowProperty(WINDOW, cv2.WND_PROP_TOPMOST, 1)
            except Exception:
                pass
            LOG.info("Camera preview window '%s' created.", WINDOW)
        while duration is None or time.perf_counter() - start < duration:
            if focus_ui_bridge is not None:
                for cmd in focus_ui_bridge.drain_commands():
                    if cmd == 'toggle-focus':
                        manager.request('toggle')
                    elif cmd == 'toggle-voice':
                        if voice_listener is not None and hasattr(voice_listener, 'enabled'):
                            voice_listener.enabled = not voice_listener.enabled
                    elif cmd == 'toggle-speech':
                        if feedback.is_muted():
                            feedback.unmute()
                        else:
                            feedback.mute(315_360_000.0)
                    elif cmd == 'toggle-chat-speech':
                        focus_ui_bridge.toggle_speak_replies()
                    elif cmd == 'stop-cancel':
                        feedback.cancel_speech()
                        if automation_proposals is not None and automation_proposals.pending is not None:
                            automation_proposals.cancel()
                        if browser_task_client is not None:
                            session_key = (
                                'local', 'voice',
                                str(getattr(manager, 'session_id', 'default')),
                            )
                            with browser_task_lock:
                                task_ids = tuple(active_local_browser_tasks.pop(session_key, ()))
                            stop_local_browser_tasks(browser_task_client, task_ids, session_key)
            manager.process_commands()
            if context_collector is not None:
                context_now = time.monotonic()
                context_collector.sample_desktop(context_now)
                context_collector.sample_session_mode(manager.mode, context_now)
                if (activity_summary_recorder is not None
                        and (last_activity_sample is None
                             or context_now - last_activity_sample >= config.companion_context.desktop_poll_seconds)):
                    try:
                        activity_summary_recorder.record(
                            context_runtime.read_snapshot(),
                            wall_time=datetime.now(timezone.utc),
                            monotonic_time=context_now,
                        )
                    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
                        LOG.warning('Daily activity sample skipped (%s)', type(exc).__name__)
                    last_activity_sample = context_now
                if companion_runtime is not None:
                    companion_runtime.tick()
            feedback.set_session(manager.session_id if manager.mode == 'focus' and not manager.quit_requested else None)
            collect_feedback()
            controls.refresh()
            if manager.quit_requested:
                break
            timeout = config.camera.read_timeout_seconds
            if duration is not None:
                timeout = min(timeout, max(.001, duration - (time.perf_counter() - start)))
            try:
                packet = capture.read(sequence, timeout)
            except CaptureError:
                if duration is not None and time.perf_counter() - start >= duration:
                    break
                raise
            dropped += max(0, packet.sequence - sequence - 1)
            sequence = packet.sequence
            consumed += 1
            if window_created:
                calibration_drag["frame_width"] = packet.image.shape[1]
                calibration_drag["frame_height"] = packet.image.shape[0]
            if (consumed - 1) % config.detector.every_n_frames:
                skipped += 1
            else:
                before = time.perf_counter()
                box = detector.detect(packet.image)
                hand_landmarks = hand_tracker.detect(packet.image, packet.monotonic_time)
                hand_frames += bool(hand_landmarks)
                gesture_count = None
                gesture_handedness = None
                if hand_landmarks:
                    handedness = getattr(hand_tracker, 'last_handedness', ())
                    hand_confidences = getattr(hand_tracker, 'last_confidences', ())
                    for hand_index, landmarks in enumerate(hand_landmarks):
                        if hand_overlaps_phone(landmarks, box):
                            continue
                        confidence = (hand_confidences[hand_index]
                                      if hand_index < len(hand_confidences) else None)
                        if (not isinstance(confidence, (int, float))
                                or not math.isfinite(confidence) or confidence < .5):
                            continue
                        side = handedness[hand_index] if hand_index < len(handedness) else None
                        count = count_fingers(landmarks, handedness=side)
                        if count is None:
                            continue
                        gesture_handedness = side
                        gesture_count = count
                        break
                triggered_gesture = gesture_recognizer.update(gesture_count, packet.monotonic_time)
                if triggered_gesture is not None:
                    gesture_controller.handle(
                        triggered_gesture,
                    )
                    if gesture_controller.mode.value != 'idle':
                        active_panel = gesture_controller.mode.value
                        active_panel_sequence += 1
                        gesture_last_action = f'{gesture_controller.mode.value.title()} mode selected'
                    if gesture_last_action is not None:
                        gesture_last_action_at = packet.monotonic_time
                if hasattr(face_tracker, 'detect_with_blendshapes'):
                    face_landmarks_list, face_blendshapes_list = face_tracker.detect_with_blendshapes(packet.image, packet.monotonic_time)
                else:
                    face_landmarks_list = face_tracker.detect(packet.image, packet.monotonic_time)
                    face_blendshapes_list = []
                face_landmarks = face_landmarks_list[0] if face_landmarks_list else []
                face_blendshapes = face_blendshapes_list[0] if face_blendshapes_list else {}
                face_frames += bool(face_landmarks)
                if context_collector is not None:
                    from integrations.desk_presence import face_center_in_region, is_camera_frame_usable
                    desk_region = context_runtime.desk_region
                    face_in_desk_region = (
                        desk_region is not None
                        and any(face_center_in_region(
                            landmarks, desk_region, packet.image.shape[1], packet.image.shape[0],
                        ) for landmarks in face_landmarks_list)
                    )
                    context_collector.observe_face(
                        face_in_desk_region, packet.monotonic_time,
                        source_available=face_tracker_available and is_camera_frame_usable(packet.image),
                    )

                phone_detected = (box is not None)
                phone_orientation = classify_phone_orientation(box, hand_landmarks, face_landmarks) if box is not None else 'NONE'
                emotion_trigger = expression_tracker.update(
                    face_blendshapes,
                    packet.monotonic_time,
                    phone_detected=phone_detected,
                    phone_posture=phone_orientation if phone_orientation != 'NONE' else None
                )
                if emotion_trigger is not None:
                    LOG.info('VISION_TRIGGER: %s at monotonic_time=%.2f', emotion_trigger, packet.monotonic_time)
                    if feedback.enabled and manager.mode == 'focus':
                        submit_expr = getattr(feedback, 'submit_expression', None)
                        if submit_expr is not None:
                            submit_expr(emotion_trigger)

                fusion_status = fusion.update(box, hand_landmarks, face_landmarks, packet.monotonic_time, packet.captured_at)
                hold_status = fusion_status.hold
                drowsiness_status = fusion_status.drowsiness
                yawn_status = fusion_status.yawn

                log_end(hold_status.ended, tag='HOLD_ENDED', event_type='phone_held')
                log_end(drowsiness_status.ended, tag='DROWSINESS_ENDED', event_type='drowsiness_microsleep')
                log_end(yawn_status.ended, tag='YAWN_ENDED', event_type='yawn')
                if fusion_status.posture_status and fusion_status.posture_status.ended:
                    log_end(fusion_status.posture_status.ended, tag='POSTURE_ENDED', event_type='slouch')

                if focus_ui_bridge is not None:
                    rms = getattr(voice_listener, 'current_rms', 0.0) if voice_listener is not None else 0.0
                    is_speaking = getattr(feedback, 'is_speaking', lambda: False)()
                    voice_enabled = getattr(voice_listener, 'enabled', False) if voice_listener is not None else False
                    focus_ui_bridge.publish(
                        packet.image,
                        mode=manager.mode,
                        audioLevel=rms,
                        voiceEnabled=voice_enabled,
                        muted=feedback.is_muted(),
                        speaking=is_speaking,
                        aiStatus=assistant_service.ai_status(),
                        elapsedSeconds=int(time.perf_counter() - start)
                    )
                    with core_connection_lock:
                        checked_at = core_connection_state['checked_at']
                        raw_core_state = effective_core_connection_state(
                            core_connection_state['health'], core_connection_state['vision'])
                    configured = ascend_client is not None and bool(ascend_character_id)
                    if not configured:
                        fairy_core_state = 'unconfigured'
                    elif checked_at is None:
                        fairy_core_state = 'connecting'
                    elif (datetime.now(timezone.utc) - checked_at).total_seconds() > 45:
                        fairy_core_state = 'stale'
                    elif raw_core_state in {'ASCEND_CONNECTED', 'CONNECTED'}:
                        fairy_core_state = 'connected'
                    elif raw_core_state and 'AUTH' in raw_core_state:
                        fairy_core_state = 'reauth-required'
                    else:
                        fairy_core_state = 'offline'
                    focus_ui_bridge.publish_core_connection(
                        configured=configured,
                        state=fairy_core_state,
                        last_checked_at=(checked_at.isoformat() if checked_at else None),
                    )
                    if face_landmarks:
                        focus_ui_bridge.publish_face(face_landmarks, packet.image.shape[1], packet.image.shape[0])
                    else:
                        focus_ui_bridge.publish_face([], 0, 0)

                    last_heard_str = ''
                    if voice_listener is not None:
                        lh_text, lh_t = getattr(voice_listener, 'last_heard', ('', 0.0))
                        if lh_text and (time.monotonic() - lh_t) < 4.0:
                            last_heard_str = lh_text

                    posture_st = fusion_status.posture_status.state if (fusion_status.posture_status is not None) else None
                    focus_ui_bridge.publish_telemetry(
                        phone_box=box,
                        hands=hand_landmarks,
                        face_landmarks=face_landmarks,
                        posture=posture_st,
                        fatigue=fusion_status.fatigue_level,
                        gesture=gesture_last_action or (f'{gesture_count} fingers' if gesture_count is not None else None),
                        active_panel=active_panel,
                        active_panel_sequence=active_panel_sequence,
                        emotion=expression_tracker.current_emotion,
                        last_heard=last_heard_str,
                        width=packet.image.shape[1],
                        height=packet.image.shape[0],
                    )

                if hold_status.started is not None:
                    if ascend_observations is not None:
                        ascend_observations.submit_phone_usage(hold_status.started)
                    try:
                        saved_id = manager.record_start(hold_status.started, event_type='phone_held', posture=hold_status.started.posture)
                    except TypeError:
                        saved_id = manager.record_start(hold_status.started)
                    confirmed += 1
                    LOG.info('HOLD_STARTED id=%d captured_at=%s confidence=%.3f '
                             'proximity_cooldown_eligible=%s distance_px=%.1f',
                             hold_status.started.id, hold_status.started.started_at.isoformat(),
                             hold_status.started.confidence, hold_status.started.alert_allowed, hold_status.distance_px)
                    if warning_state_machine is not None and hold_status.started.alert_allowed:
                        warning_state_machine.handle_trigger(SensoryTriggerType.PHONE)
                    if feedback.enabled and manager.mode == 'focus' and hold_status.started.alert_allowed:
                        session_id, event_mode, metadata = database.feedback_context(saved_id)
                        if event_mode == 'focus':
                            feedback.submit(saved_id, session_id, RoastContext(**metadata))

                if drowsiness_status.started is not None:
                    try:
                        manager.record_start(drowsiness_status.started, event_type='drowsiness_microsleep', posture='none')
                    except Exception as exc:
                        LOG.debug('Drowsiness record_start: %s', exc)
                    if warning_state_machine is not None:
                        warning_state_machine.handle_trigger(SensoryTriggerType.FATIGUE)

                if yawn_status.started is not None:
                    try:
                        manager.record_start(yawn_status.started, event_type='yawn', posture='none')
                    except Exception as exc:
                        LOG.debug('Yawn record_start: %s', exc)
                    if warning_state_machine is not None:
                        warning_state_machine.handle_trigger(SensoryTriggerType.FATIGUE)

                if fusion_status.posture_status and fusion_status.posture_status.started is not None:
                    try:
                        manager.record_start(fusion_status.posture_status.started, event_type='slouch', posture='none')
                    except Exception as exc:
                        LOG.debug('Posture record_start: %s', exc)
                    if warning_state_machine is not None:
                        warning_state_machine.handle_trigger(SensoryTriggerType.SLOUCH)

                # Multimodal alert feedback (drowsiness, yawn, or slouch)
                alert = fusion_status.primary_alert
                if alert is not None and alert.event_type != 'phone_held':
                    try:
                        active_rec = manager._active_by_type.get(alert.event_type)
                        if active_rec is not None:
                            saved_id = active_rec[1]
                            if feedback.enabled and manager.mode == 'focus':
                                session_id, event_mode, metadata = database.feedback_context(saved_id)
                                if event_mode == 'focus':
                                    feedback.submit(saved_id, session_id, RoastContext(**metadata))
                    except Exception as exc:
                        LOG.debug('Safety alert feedback submission: %s', exc)

                if hold_status.active is not None:
                    manager.record_update(hold_status.active, event_type='phone_held')
                if drowsiness_status.active is not None:
                    manager.record_update(drowsiness_status.active, event_type='drowsiness_microsleep')
                if yawn_status.active is not None:
                    manager.record_update(yawn_status.active, event_type='yawn')
                if fusion_status.posture_status and fusion_status.posture_status.active:
                    manager.record_update(fusion_status.posture_status.active, event_type='slouch')

                latency = time.perf_counter() - before
                inference_seconds += latency
                processed += 1
                if box is not None:
                    detections += 1
                    LOG.info('PHONE_VISIBLE captured_at=%s frame=%d confidence=%.3f '
                             'box_xyxy=%s inference_ms=%.2f frame_age_ms=%.2f',
                             packet.captured_at.isoformat(), packet.sequence, box.confidence,
                             box.xyxy, latency * 1000,
                             (time.perf_counter() - packet.monotonic_time) * 1000)
                if config.runtime.preview:
                    display = packet.image.copy()
                    if context_runtime is not None:
                        desk_region = context_runtime.desk_region
                        if desk_region is not None:
                            height, width = display.shape[:2]
                            cv2.rectangle(
                                display,
                                (round(desk_region.left * width), round(desk_region.top * height)),
                                (round(desk_region.right * width), round(desk_region.bottom * height)),
                                (60, 220, 120), 2,
                            )
                        if context_runtime.desk_calibrating:
                            if calibration_drag["start"] is not None and calibration_drag["end"] is not None:
                                cv2.rectangle(display, calibration_drag["start"], calibration_drag["end"],
                                              (0, 210, 255), 2)
                            notice = calibration_notice["text"] or "Drag a rectangle around your desk; Esc cancels"
                            cv2.putText(display, notice,
                                        (10, display.shape[0] - 18), cv2.FONT_HERSHEY_SIMPLEX,
                                        .55, (0, 210, 255), 2)
                    for hand in hand_landmarks:
                        for index, (x, y, z) in enumerate(hand):
                            if not (float('-inf') < x < float('inf') and float('-inf') < y < float('inf')):
                                continue
                            color = (0, 220, 255) if index in HOLD_LANDMARKS else (220, 160, 60)
                            cv2.circle(display, (round(x), round(y)), 3, color, -1)
                    if face_landmarks:
                        for idx in LEFT_EYE_LANDMARKS + RIGHT_EYE_LANDMARKS:
                            if idx < len(face_landmarks):
                                fx, fy = face_landmarks[idx][:2]
                                cv2.circle(display, (round(fx), round(fy)), 2, (0, 255, 255), -1)
                        for idx in MOUTH_LANDMARKS:
                            if idx < len(face_landmarks):
                                fx, fy = face_landmarks[idx][:2]
                                cv2.circle(display, (round(fx), round(fy)), 2, (255, 100, 255), -1)
                    if box is not None:
                        x1, y1, x2, y2 = (round(v) for v in box.xyxy)
                        cv2.rectangle(display, (x1, y1), (x2, y2), (0, 210, 80), 2)
                        tag = f'cell phone {box.confidence:.2f}'
                        if phone_orientation != 'NONE':
                            tag += f' [{phone_orientation.replace("PHONE_", "")}]'
                        elif hold_status.posture != 'none':
                            tag += f' [{hold_status.posture.upper()}]'
                        cv2.putText(display, tag, (max(0, x1), max(20, y1 - 8)),
                                    cv2.FONT_HERSHEY_SIMPLEX, .55, (0, 210, 80), 2)
                    fps = processed / max(time.perf_counter() - start, 1e-9)
                    hold_label = f'{hold_status.state} {hold_status.consecutive_frames}/{config.hold.threshold_frames}'
                    if hold_status.active is not None:
                        hold_label = f'holding {hold_status.active.duration_seconds:.1f}s'
                    cv2.putText(display, f'{hold_label} | {fps:.1f} FPS', (10, 25),
                                cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 2)

                    with core_connection_lock:
                        core_label, core_tone = core_status_indicator(
                            configured=ascend_client is not None,
                            state=effective_core_connection_state(
                                core_connection_state['health'], core_connection_state['vision']),
                        )
                        vision_label, vision_tone = vision_presence_indicator(
                            configured=ascend_client is not None and bool(ascend_character_id),
                            state=vision_presence_state['value'],
                        )
                    gesture_lines = gesture_overlay_lines(
                        finger_count=gesture_count,
                        handedness=gesture_handedness,
                        recognizer=gesture_recognizer,
                        controller=gesture_controller,
                        feedback_muted=feedback.is_muted(),
                        now=packet.monotonic_time,
                        last_action=(gesture_last_action
                                     if packet.monotonic_time - gesture_last_action_at <= 3.0 else None),
                        core_status=core_label,
                        vision_status=vision_label,
                    )
                    panel_x = max(10, display.shape[1] - 470)
                    panel_y2 = 232
                    cv2.rectangle(display, (panel_x - 12, 8), (display.shape[1] - 8, panel_y2), (16, 18, 24), -1)
                    cv2.rectangle(display, (panel_x - 12, 8), (display.shape[1] - 8, panel_y2), (100, 110, 125), 2)
                    for index, line in enumerate(gesture_lines):
                        if index == 0:
                            color, scale = (255, 220, 80), .75
                        elif index == 1 and 'MUTED' in line:
                            color, scale = (0, 0, 255), .64
                        elif index == 1:
                            color = {'good': (0, 255, 120), 'warning': (0, 210, 255),
                                     'bad': (0, 0, 255), 'muted': (180, 180, 180)}[core_tone]
                            scale = .64
                        elif index == 2:
                            color = {'good': (0, 255, 120), 'warning': (0, 210, 255),
                                     'bad': (0, 0, 255), 'muted': (180, 180, 180)}[vision_tone]
                            scale = .64
                        else:
                            color, scale = (245, 245, 245), .62
                        cv2.putText(display, line, (panel_x, 38 + index * 30),
                                    cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2)

                    fatigue_colors = {
                        'critical_drowsy': (0, 0, 255),
                        'moderate_fatigue': (0, 140, 255),
                        'mild_fatigue': (0, 255, 255),
                        'alert': (0, 255, 120)
                    }
                    fatigue_color = fatigue_colors.get(fusion_status.fatigue_level, (200, 255, 200))
                    # Display adaptive eye/lip status or calibration progress
                    if not drowsiness_status.calibrated or not yawn_status.calibrated:
                        cal_pct = int(min(drowsiness_status.calibration_progress, yawn_status.calibration_progress) * 100)
                        fatigue_label = (f'CALIBRATING EYES & LIPS ({cal_pct}%) | EAR: {drowsiness_status.ear:.2f} '
                                         f'MAR: {yawn_status.mar:.2f}')
                        fatigue_color = (0, 255, 255)
                    else:
                        fatigue_label = (f'EAR: {drowsiness_status.ear:.2f}/{drowsiness_status.effective_threshold:.2f} ({drowsiness_status.state.upper()}) | '
                                         f'MAR: {yawn_status.mar:.2f}/{yawn_status.effective_threshold:.2f} ({yawn_status.state.upper()}) | '
                                         f'{fusion_status.fatigue_level.upper()}')
                    cv2.putText(display, fatigue_label, (10, 50),
                                cv2.FONT_HERSHEY_SIMPLEX, .55, fatigue_color, 2)

                    posture_status = fusion_status.posture_status
                    if posture_status is not None:
                        if not posture_status.calibrated:
                            cal_text = f'POSTURE: CALIBRATING ({int(posture_status.calibration_progress * 100)}%)'
                            cv2.putText(display, cal_text, (10, 75),
                                        cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 0), 2)
                        else:
                            p_color = (0, 0, 255) if posture_status.state == 'slouched' else (0, 165, 255) if posture_status.state == 'slouching' else (0, 255, 120)
                            p_text = f'POSTURE: {posture_status.state.upper()}'
                            if posture_status.state == 'slouching':
                                p_text += f' ({posture_status.consecutive_frames}/{config.posture.consec_frames})'
                            cv2.putText(display, p_text, (10, 75),
                                        cv2.FONT_HERSHEY_SIMPLEX, .55, p_color, 2)

                    # Mic status badge and dynamic capturing indicator
                    mic_label = 'OFF'
                    mic_color = (150, 150, 150)
                    rms_level = getattr(voice_listener, 'current_rms', 0.0) if voice_listener is not None else 0.0
                    threshold = getattr(config.voice_commands, 'energy_threshold', 0.005)

                    if feedback.is_muted():
                        mic_label = 'MUTED'
                        mic_color = (0, 0, 255)
                    elif voice_listener is not None and getattr(voice_listener, 'enabled', False):
                        if getattr(voice_listener, 'is_transcribing', False):
                            mic_label = 'TRANSCRIBING...'
                            mic_color = (0, 255, 255)  # Yellow
                        elif getattr(voice_listener, 'is_recording', False):
                            mic_label = f'CAPTURING SPEECH (lvl={rms_level:.3f})'
                            mic_color = (0, 120, 255)  # Orange/Red live active capture
                        elif getattr(feedback, 'is_speaking', lambda: False)():
                            mic_label = 'TTS PLAYING'
                            mic_color = (255, 150, 0)  # Cyan
                        else:
                            mic_label = f'LISTENING (lvl={rms_level:.3f}/{threshold:.3f})'
                            mic_color = (0, 255, 120)  # Green

                    # Draw Mic status pill/text
                    cv2.putText(display, f'{manager.mode.upper()} | Blinks: {drowsiness_status.blink_count} | Yawns: {yawn_status.yawn_count} | Holds: {confirmed}', (10, 100),
                                cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 2)
                    cv2.putText(display, f'MIC: {mic_label}', (10, 125),
                                cv2.FONT_HERSHEY_SIMPLEX, .55, mic_color, 2)

                    # Draw Emotion status
                    current_emo = expression_tracker.current_emotion
                    emo_colors = {
                        'fatigue': (0, 140, 255),
                        'stressed': (0, 0, 255),
                        'smiling': (0, 255, 120),
                        'neutral': (200, 200, 200)
                    }
                    emo_color = emo_colors.get(current_emo, (200, 200, 200))
                    cv2.putText(display, f'EMOTION: {current_emo.upper()}', (10, 150),
                                cv2.FONT_HERSHEY_SIMPLEX, .55, emo_color, 2)

                    # Draw Gemini-style sleek audio waveform pill at bottom of screen
                    h, w = display.shape[:2]
                    pill_w = min(360, w - 40)
                    pill_h = 36
                    pill_x1 = (w - pill_w) // 2
                    pill_y1 = h - 50
                    pill_x2 = pill_x1 + pill_w
                    pill_y2 = pill_y1 + pill_h

                    # Semi-transparent dark pill background (glassmorphism effect)
                    overlay = display.copy()
                    cv2.rectangle(overlay, (pill_x1, pill_y1), (pill_x2, pill_y2), (22, 22, 24), -1)
                    cv2.rectangle(overlay, (pill_x1, pill_y1), (pill_x2, pill_y2), (55, 55, 60), 1)
                    cv2.addWeighted(overlay, 0.85, display, 0.15, 0, display)

                    # Draw Plus (+) icon on left
                    cv2.putText(display, '+', (pill_x1 + 12, pill_y1 + 24),
                                cv2.FONT_HERSHEY_SIMPLEX, .65, (160, 160, 165), 1)

                    # Dynamic audio waveform equalizer bars
                    waveform_samples = getattr(voice_listener, 'waveform', []) if voice_listener is not None else []
                    num_bars = 32
                    bar_spacing = 6
                    bar_width = 2
                    wf_start_x = pill_x1 + 42
                    center_y = pill_y1 + (pill_h // 2)

                    is_rec = getattr(voice_listener, 'is_recording', False) if voice_listener is not None else False
                    is_trans = getattr(voice_listener, 'is_transcribing', False) if voice_listener is not None else False
                    is_muted = feedback.is_muted()

                    # Pad or sample waveform history into fixed bar count
                    if len(waveform_samples) >= num_bars:
                        recent_wf = waveform_samples[-num_bars:]
                    else:
                        recent_wf = [0.0] * (num_bars - len(waveform_samples)) + list(waveform_samples)

                    # Render vertical audio bars
                    for bi, amp in enumerate(recent_wf):
                        bx = wf_start_x + bi * bar_spacing
                        # Scale amplitude: base minimum dot height of 2px, up to 26px when speaking
                        norm_amp = min(1.0, amp / max(1e-4, threshold * 3.5))
                        if is_muted:
                            bar_h = 2
                            bar_color = (80, 80, 180)
                        elif is_trans:
                            bar_h = int(6 + 4 * math.sin(time.perf_counter() * 8 + bi * 0.5))
                            bar_color = (0, 220, 255)
                        elif is_rec:
                            bar_h = max(3, min(26, int(norm_amp * 26)))
                            bar_color = (240, 240, 250) if norm_amp > 0.4 else (180, 180, 190)
                        else:
                            # Idle subtle dot
                            bar_h = max(2, int(norm_amp * 8))
                            bar_color = (120, 120, 130)

                        top_y = max(pill_y1 + 4, center_y - (bar_h // 2))
                        bot_y = min(pill_y2 - 4, center_y + (bar_h // 2))
                        cv2.line(display, (bx, top_y), (bx, bot_y), bar_color, bar_width)

                    # Action button on right (Stop square or Up arrow)
                    btn_center_x = pill_x2 - 20
                    if is_rec or is_trans:
                        # Stop square in circle
                        cv2.circle(display, (btn_center_x, center_y), 11, (50, 50, 55), -1)
                        cv2.rectangle(display, (btn_center_x - 4, center_y - 4), (btn_center_x + 4, center_y + 4), (240, 240, 245), -1)
                    else:
                        # Circle with up arrow
                        cv2.circle(display, (btn_center_x, center_y), 11, (180, 90, 30), -1)  # Blue circle
                        cv2.putText(display, '^', (btn_center_x - 5, center_y + 5),
                                    cv2.FONT_HERSHEY_SIMPLEX, .45, (255, 255, 255), 1)

                    # Show live subtitle toast above the pill if user recently spoke (persists for 4.0 seconds)
                    if voice_listener is not None:
                        last_heard_text, last_heard_t = getattr(voice_listener, 'last_heard', ('', 0.0))
                        if last_heard_text and (time.monotonic() - last_heard_t) < 4.0:
                            toast_y = pill_y1 - 12
                            t_size = cv2.getTextSize(f'Heard: "{last_heard_text}"', cv2.FONT_HERSHEY_SIMPLEX, .52, 2)[0]
                            cv2.rectangle(display, (pill_x1, toast_y - 20), (min(w - 10, pill_x1 + t_size[0] + 16), toast_y + 4), (24, 24, 26), -1)
                            cv2.putText(display, f'Heard: "{last_heard_text}"', (pill_x1 + 8, toast_y - 4),
                                        cv2.FONT_HERSHEY_SIMPLEX, .52, (0, 255, 255), 2)

                    # High-visibility alert toast on primary alert trigger
                    if fusion_status.primary_alert is not None:
                        alert_text = f'SAFETY ALERT: {fusion_status.primary_alert.event_type.upper()}'
                        cv2.putText(display, alert_text, (10, 150),
                                    cv2.FONT_HERSHEY_SIMPLEX, .65, (0, 0, 255), 2)

                    cv2.imshow(WINDOW, display)
            if config.runtime.preview:
                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord('q'), ord('Q')):
                    if context_runtime is not None and context_runtime.desk_calibrating:
                        context_runtime.cancel_desk_calibration()
                        calibration_drag["start"] = None
                        calibration_drag["end"] = None
                        continue
                    break
                if key == ord(' '):
                    manager.request('toggle')
                if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                    break
            if time.perf_counter() >= next_report:
                report()
                next_report = time.perf_counter() + config.runtime.stats_interval_seconds
    except KeyboardInterrupt:
        LOG.info('Stopping at user request')
    except Exception:
        finish_reason = 'error'
        raise
    finally:
        try:
            fin = fusion.finish(finish_reason)
            log_end(fin.get('hold'), tag='HOLD_ENDED', event_type='phone_held')
            log_end(fin.get('drowsiness'), tag='DROWSINESS_ENDED', event_type='drowsiness_microsleep')
            log_end(fin.get('yawn'), tag='YAWN_ENDED', event_type='yawn')
            log_end(fin.get('posture'), tag='POSTURE_ENDED', event_type='slouch')
        finally:
            try:
                resources.close()
            finally:
                if window_created:
                    cv2.destroyAllWindows()
                report()



def main(argv=None):
    parser = argparse.ArgumentParser(description='Phone Watch focus-only roast feedback (Phase 4)')
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.yaml'))
    parser.add_argument('--env-file', type=Path,
                        help='load provider credentials from this owner-managed env file')
    parser.add_argument('--camera', type=int, help='override camera index')
    parser.add_argument('--confidence', type=float, help='override confidence, 0 to 1')
    parser.add_argument('--preview', action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument('--duration', type=float, help='stop after this many seconds, excluding warmup')
    parser.add_argument('--download-model', action='store_true', help='download both models and exit without opening camera')
    parser.add_argument('--init-db', action='store_true', help='initialize SQLite schema and exit without camera access')
    parser.add_argument('--focus', action='store_true', help='explicitly start in focus mode')
    parser.add_argument('--tray', action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument('--hotkey', action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument('--feedback', action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument('--fairy-ui', action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument('--test-speech', nargs='?', const='Phone Watch speech is ready.', type=str, default=None, help='speak an offline diagnostic line (optionally provide custom text), without camera/API/database')
    parser.add_argument('--test-feedback', action='store_true', help='make one real API request with synthetic counts and speak it; no camera/database')
    parser.add_argument('--test-chat', type=str, help='speak a test conversational query to Gemini and speak response via TTS; no camera/database')
    parser.add_argument('--test-trigger', type=str, default=None, help='speak a test reaction trigger (e.g. phone_detected, fatigue, stressed, smiling)')
    parser.add_argument('--test-screen-audit', action='store_true', help='execute one ephemeral screen audit with vision classification and guaranteed deletion')
    parser.add_argument('--test-spotify', type=str, nargs='?', const='toggle', default=None, help='control Spotify (toggle, play, pause, status) and speak response')
    parser.add_argument('--test-ascend', action='store_true', help='call the Real Ascend health endpoint and send one test command')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        from assistant.runtime_environment import load_runtime_environment
        load_runtime_environment(args.config, args.env_file)
        config = load_config(args.config)
        if args.camera is not None:
            config = replace(config, camera=replace(config.camera, index=args.camera))
        if args.confidence is not None:
            config = replace(config, detector=replace(config.detector, confidence=args.confidence))
        fairy_ui_requested = args.fairy_ui
        if fairy_ui_requested is None:
            fairy_ui_requested = bool(args.focus)
        if args.preview is not None:
            config = replace(config, runtime=replace(config.runtime, preview=args.preview))
        elif fairy_ui_requested:
            config = replace(config, runtime=replace(config.runtime, preview=False))
        if args.duration is not None:
            number('duration', args.duration, .01)
        if args.focus:
            config = replace(config, sessions=replace(config.sessions, initial_mode='focus'))
        if args.tray is not None:
            config = replace(config, sessions=replace(config.sessions, tray=args.tray))
        if args.hotkey is not None:
            config = replace(config, sessions=replace(config.sessions, hotkey_enabled=args.hotkey))
        if args.feedback is not None:
            config = replace(config, feedback=replace(config.feedback, enabled=args.feedback))
        if args.test_speech is not None:
            text = args.test_speech if isinstance(args.test_speech, str) and args.test_speech.strip() else 'Phone Watch speech is ready.'
            if getattr(config.feedback, 'tts_engine', 'kokoro_onnx') == 'kokoro_onnx':
                from tts_engine import KokoroSpeaker
                speaker = KokoroSpeaker(config.feedback)
            else:
                from speech import OfflineSpeaker
                speaker = OfflineSpeaker(config.feedback)
            try:
                result = speaker.speak(text, lambda: False)
                LOG.info('Speech test: started=%s completed=%s', result.started, result.completed)
                return 0 if result.completed else 1
            finally:
                speaker.close()
        if args.test_trigger is not None:
            trigger_name = args.test_trigger.strip() or 'phone_detected'
            from feedback_router import get_expression_prompt_context, get_fallback_expression_reply
            from llm_router import get_router

            if getattr(config.feedback, 'tts_engine', 'kokoro_onnx') == 'kokoro_onnx':
                from tts_engine import KokoroSpeaker
                speaker = KokoroSpeaker(config.feedback)
            else:
                from speech import OfflineSpeaker
                speaker = OfflineSpeaker(config.feedback)

            try:
                router = get_router()
                prompt_ctx = get_expression_prompt_context(trigger_name)
                LOG.info('Testing trigger "%s" with context: "%s"', trigger_name, prompt_ctx)
                reply = None
                try:
                    reply = router.generate_response(prompt=prompt_ctx, task="fast", max_tokens=50)
                except Exception as err:
                    LOG.warning('Router trigger response failed (%s), using fallback line', err)
                if not reply:
                    reply = get_fallback_expression_reply(trigger_name)
                LOG.info('Reaction generated: "%s"', reply)
                result = speaker.speak(reply, lambda: False)
                LOG.info('Trigger test speech: started=%s completed=%s', result.started, result.completed)
                return 0 if result.completed else 1
            finally:
                speaker.close()
        if args.test_feedback:
            service = FeedbackService(replace(config.feedback, enabled=True), 0)
            try:
                service.start()
                service.set_session(1)
                if not service.submit(1, 1, RoastContext(3, 1, 10., '14:20')):
                    return 1
                # Account for initial engine warmup (6s) + request timeout (10s) + synthesis + speech timeout (20s)
                deadline = time.monotonic() + config.feedback.request_timeout_seconds + config.feedback.speech_timeout_seconds + 15
                while time.monotonic() < deadline:
                    results = service.drain()
                    if results:
                        result = results[0]
                        LOG.info('Feedback test: completed=%s error=%s text=%s', result.completed, result.error, result.text)
                        return 0 if result.completed else 1
                    time.sleep(.05)
                LOG.error('Feedback diagnostic timed out')
                return 1
            finally:
                service.close()
        if args.test_chat:
            service = FeedbackService(replace(config.feedback, enabled=True), 0)
            try:
                service.start()
                ctx = ConversationContext(
                    user_query=args.test_chat,
                    mode='focus',
                    phone_pickups=1,
                    microsleep_events=0,
                    yawns=0,
                    slouch_events=2,
                    session_duration_minutes=15.0
                )
                max_w = getattr(config.voice_commands, 'max_reply_words', 25)
                if not service.submit_chat(args.test_chat, ctx, max_words=max_w):
                    LOG.error('Failed to queue test chat job')
                    return 1
                LOG.info('Chat test submitted for: "%s"', args.test_chat)
                deadline = time.monotonic() + config.feedback.request_timeout_seconds + config.feedback.speech_timeout_seconds + 30
                time.sleep(1.0)
                while time.monotonic() < deadline:
                    if service._jobs.empty() and not service._chat_busy and not service.is_speaking():
                        LOG.info('Conversational test completed successfully.')
                        return 0
                    time.sleep(0.2)
                LOG.error('Conversational test timed out')
                return 1
            finally:
                service.close()
        if args.test_screen_audit:
            from screen_auditor import ScreenAuditor
            if getattr(config.feedback, 'tts_engine', 'kokoro_onnx') == 'kokoro_onnx':
                from tts_engine import KokoroSpeaker
                speaker = KokoroSpeaker(config.feedback)
            else:
                from speech import OfflineSpeaker
                speaker = OfflineSpeaker(config.feedback)

            class _DirectSpeakerFeedback:
                def submit_expression(self, event_name):
                    from feedback_router import get_expression_prompt_context, get_fallback_expression_reply
                    from llm_router import get_router
                    prompt_ctx = get_expression_prompt_context(event_name)
                    reply = None
                    try:
                        router = get_router()
                        reply = router.generate_response(prompt=prompt_ctx, task="fast", max_tokens=50)
                    except Exception as err:
                        LOG.warning("LLM router failed (%s), using fallback line", err)
                    if not reply:
                        reply = get_fallback_expression_reply(event_name)
                    LOG.info("Auditor spoken reaction: '%s'", reply)
                    speaker.speak(reply, lambda: False)

            try:
                auditor = ScreenAuditor(feedback_service=_DirectSpeakerFeedback())
                category, observation = auditor.audit_once()
                LOG.info("Screen audit completed successfully: category=%s observation='%s'", category, observation)
                return 0
            finally:
                speaker.close()

        if args.test_spotify is not None:
            action = args.test_spotify.strip().lower()
            from tools.spotify_tool import spotify_play_pause, get_spotify_status
            if action == 'status':
                st = get_spotify_status()
                LOG.info("Spotify Status: %s", st)
                return 0
            success, msg = spotify_play_pause(action if action in ('play', 'pause') else None)
            LOG.info("Spotify action result: success=%s, msg='%s'", success, msg)
            if getattr(config.feedback, 'tts_engine', 'kokoro_onnx') == 'kokoro_onnx':
                from tts_engine import KokoroSpeaker
                speaker = KokoroSpeaker(config.feedback)
            else:
                from speech import OfflineSpeaker
                speaker = OfflineSpeaker(config.feedback)
            try:
                speaker.speak(msg, lambda: False)
            finally:
                speaker.close()
            return 0 if success else 1

        if args.test_ascend:
            from integrations.ascend_client import AscendClient, AscendConnectionState
            ascend_config = config.ascend
            if not ascend_config.enabled:
                LOG.info('Ascend integration is disabled')
                return 1
            base_url = os.getenv(ascend_config.base_url_env, '').strip()
            if not base_url:
                LOG.error('Ascend base URL is not configured (%s)', ascend_config.base_url_env)
                return 1
            client = AscendClient(
                base_url=base_url,
                api_token=os.getenv(ascend_config.api_token_env),
                timeout_seconds=ascend_config.timeout_seconds,
                health_path=ascend_config.health_path,
                command_path=ascend_config.command_path,
                event_path=ascend_config.event_path,
            )
            health = client.get_status()
            LOG.info('Ascend Connection: %s', health.state.value)
            if health.state is not AscendConnectionState.CONNECTED:
                LOG.error('Ascend health check failed: %s', health.error or health.payload)
                return 1
            result = client.send_command(
                "What missions do I have today?", source='phone',
                character_id=os.getenv(ascend_config.character_id_env),
            )
            LOG.info('Ascend test command: state=%s response=%s', result.state.value,
                     result.message or result.payload)
            return 0 if result.state is AscendConnectionState.CONNECTED else 1
        if args.init_db:
            with Database(config.storage.database, timeout=config.storage.busy_timeout_seconds):
                LOG.info('Database initialized: %s', config.storage.database)
            return 0
        if args.download_model:
            LOG.info('Local model ready: %s', download_model(config.detector.model))
            LOG.info('Local hand model ready: %s', download_hand_model(config.hands.model))
            LOG.info('Local face model ready: %s', download_face_model(config.face.model))
            return 0
        run(config, duration=args.duration, fairy_ui=fairy_ui_requested)
        return 0
    except KeyboardInterrupt:
        LOG.info('Startup interrupted')
        return 130
    except Exception as exc:
        LOG.error('%s: %s', type(exc).__name__, exc)
        return 1


if __name__ == '__main__':
    # Route service/terminal termination through the same cleanup as Ctrl+C.
    def terminate(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    raise SystemExit(main())
