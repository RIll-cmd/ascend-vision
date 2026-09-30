"""Independent local dashboard: no detector, camera, API key or writer startup."""
import argparse
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit
import webbrowser

from flask import Flask, abort, jsonify, render_template, request

from config import load_config
from assistant.activity_summary import ActivityHistoryStore, RETENTION_DAYS
from assistant.memory import MemoryStore
from dashboard_stats import DashboardError, get_zone, read_stats
from integrations.ascend_client import AscendClient, AscendConnectionState
from integrations.chat_ipc import ChatIpcQueue
from integrations.context_ipc import ContextPipeClient
from integrations.vision_context import VisionAuthContext, VisionContextStore
from integrations.vision_token_store import VisionToken, VisionTokenStore

LOG = logging.getLogger(__name__)


def create_app(config, chat_queue=None, memory_store=None, context_client=None,
               activity_history_store=None, browser_client=None):
    app = Flask(__name__)
    app.config.update(TRUSTED_HOSTS=['127.0.0.1', 'localhost'])
    queue = chat_queue if chat_queue is not None else ChatIpcQueue(
        Path(config.storage.database).parent / 'chat_ipc.db')
    if context_client is None:
        try:
            context_client = ContextPipeClient()
        except (ImportError, OSError, RuntimeError, ValueError):
            context_client = None
    if memory_store is None:
        try:
            memory_store = MemoryStore(Path(config.storage.database).parent / 'assistant_memory.db')
        except Exception as exc:
            LOG.warning('Dashboard memory unavailable (%s)', type(exc).__name__)
    if activity_history_store is None:
        try:
            activity_history_store = ActivityHistoryStore(
                Path(config.storage.database).parent / 'activity_history.db',
                timeout_seconds=config.storage.busy_timeout_seconds,
            )
        except Exception as exc:
            LOG.warning('Activity history unavailable (%s)', type(exc).__name__)

    def memory_error(exc):
        if isinstance(exc, ValueError):
            return jsonify(error=str(exc)), 400
        LOG.warning('Dashboard memory request failed (%s)', type(exc).__name__)
        return jsonify(error='Vision memory is temporarily unavailable.'), 503

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify(error='Request is too large.'), 400

    @app.get('/api/context')
    def current_context():
        if context_client is None:
            return jsonify(error='Laptop context is unavailable.'), 503
        try:
            return jsonify(snapshot=context_client.snapshot())
        except Exception as exc:
            LOG.info('Laptop context read failed (%s)', type(exc).__name__)
            return jsonify(error='Laptop context is unavailable.'), 503

    @app.get('/api/context/desk-region')
    def desk_region_status():
        if context_client is None:
            return jsonify(error='Laptop context is unavailable.'), 503
        try:
            return jsonify(desk_region=context_client.desk_region_status())
        except Exception as exc:
            LOG.info('Desk calibration state read failed (%s)', type(exc).__name__)
            return jsonify(error='Laptop context is unavailable.'), 503

    @app.get('/api/context/companion-decisions')
    def companion_decisions():
        if context_client is None:
            return jsonify(error='Laptop context is unavailable.'), 503
        try:
            return jsonify(decisions=context_client.companion_decisions())
        except Exception as exc:
            LOG.info('Companion decision read failed (%s)', type(exc).__name__)
            return jsonify(error='Laptop context is unavailable.'), 503

    @app.post('/api/context/desk-region/calibration')
    def desk_region_calibration():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'enabled'} or type(payload['enabled']) is not bool:
            return jsonify(error='enabled must be a boolean.'), 400
        if context_client is None:
            return jsonify(error='Laptop context is unavailable.'), 503
        try:
            if payload['enabled']:
                if not context_client.begin_desk_calibration():
                    return jsonify(error='Calibration cannot start. Resume context and make sure the live preview is available.'), 409
                return jsonify(calibrating=True)
            context_client.cancel_desk_calibration()
            return jsonify(calibrating=False)
        except Exception as exc:
            LOG.info('Desk calibration command failed (%s)', type(exc).__name__)
            return jsonify(error='Laptop context is unavailable.'), 503
    @app.post('/api/context/intent')
    def declare_context_intent():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or payload.keys() - {'intent', 'duration_seconds'}:
            return jsonify(error='Invalid context correction.'), 400
        intent = payload.get('intent')
        if intent not in {'focus', 'break', 'research', 'meeting'}:
            return jsonify(error='Choose focus, break, research, or meeting.'), 400
        duration = payload.get('duration_seconds', 900)
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not 1 <= duration <= 12 * 60 * 60):
            return jsonify(error='Duration must be between 1 second and 12 hours.'), 400
        if context_client is None:
            return jsonify(error='Laptop context is unavailable.'), 503
        try:
            declaration_id = context_client.declare_intent(intent, duration_seconds=duration)
            return jsonify(declarationId=declaration_id)
        except Exception as exc:
            LOG.info('Laptop context correction failed (%s)', type(exc).__name__)
            return jsonify(error='Laptop context is unavailable.'), 503

    @app.post('/api/context/clear')
    def clear_context():
        payload = request.get_json(silent=True)
        if payload not in ({}, None):
            return jsonify(error='Invalid clear-context request.'), 400
        if context_client is None:
            return jsonify(error='Laptop context is unavailable.'), 503
        try:
            context_client.clear()
            return jsonify(cleared=True)
        except Exception as exc:
            LOG.info('Laptop context clear failed (%s)', type(exc).__name__)
            return jsonify(error='Laptop context is unavailable.'), 503

    @app.post('/api/context/intent/clear')
    def undo_context_intent():
        payload = request.get_json(silent=True)
        if payload not in ({}, None):
            return jsonify(error='Invalid intent undo request.'), 400
        if context_client is None:
            return jsonify(error='Laptop context is unavailable.'), 503
        try:
            context_client.clear_intent()
            return jsonify(cleared=True)
        except Exception as exc:
            LOG.info('Laptop context intent undo failed (%s)', type(exc).__name__)
            return jsonify(error='Laptop context is unavailable.'), 503

    @app.post('/api/context/snooze')
    def snooze_context():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or payload.keys() - {'duration_seconds'}:
            return jsonify(error='Invalid snooze request.'), 400
        duration = payload.get('duration_seconds', 1800)
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not 1 <= duration <= 12 * 60 * 60):
            return jsonify(error='Snooze must be between 1 second and 12 hours.'), 400
        if context_client is None:
            return jsonify(error='Laptop context is unavailable.'), 503
        try:
            context_client.set_snooze(duration_seconds=duration)
            return jsonify(snoozed=True)
        except Exception as exc:
            LOG.info('Laptop context snooze failed (%s)', type(exc).__name__)
            return jsonify(error='Laptop context is unavailable.'), 503

    @app.post('/api/context/snooze/clear')
    def clear_context_snooze():
        payload = request.get_json(silent=True)
        if payload not in ({}, None):
            return jsonify(error='Invalid snooze clear request.'), 400
        if context_client is None:
            return jsonify(error='Laptop context is unavailable.'), 503
        try:
            context_client.clear_snooze()
            return jsonify(cleared=True)
        except Exception as exc:
            LOG.info('Laptop context snooze clear failed (%s)', type(exc).__name__)
            return jsonify(error='Laptop context is unavailable.'), 503

    @app.post('/api/context/pause')
    def pause_context():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'paused'} or type(payload['paused']) is not bool:
            return jsonify(error='paused must be a boolean.'), 400
        if context_client is None:
            return jsonify(error='Laptop context is unavailable.'), 503
        try:
            context_client.set_paused(payload['paused'])
            return jsonify(paused=payload['paused'])
        except Exception as exc:
            LOG.info('Laptop context pause failed (%s)', type(exc).__name__)
            return jsonify(error='Laptop context is unavailable.'), 503

    @app.before_request
    def local_requests_only():
        max_bytes = 11 * 1024 * 1024 if (
            request.path == '/api/browser/tasks'
            and getattr(getattr(config, 'browser_automation', None), 'b3_enabled', False)
        ) else 20_000 if request.path.startswith(('/api/chat/', '/api/browser/')) else 1_024
        request.max_content_length = max_bytes
        if request.content_length is not None:
            if request.content_length > max_bytes:
                abort(413)
        if request.headers.get('Sec-Fetch-Site') == 'cross-site':
            abort(403)
        origin = request.headers.get('Origin')
        if origin and origin != request.host_url.rstrip('/'):
            abort(403)

    @app.after_request
    def private_response(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        core_url = os.getenv(config.ascend.base_url_env, '').strip().rstrip('/')
        connect_sources = "'self'"
        if _is_local_core_url(core_url):
            parsed = urlsplit(core_url)
            connect_sources = f"{connect_sources} {parsed.scheme}://{parsed.netloc}"
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
            f"connect-src {connect_sources}; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    @app.get('/')
    def index():
        today = datetime.now(timezone.utc).astimezone(get_zone(config.dashboard.timezone)).date()
        core_url = os.getenv(config.ascend.base_url_env, '').strip().rstrip('/')
        return render_template('dashboard.html', today=today.isoformat(),
            start=(today-timedelta(days=config.dashboard.default_days-1)).isoformat(),
            refresh=config.dashboard.refresh_seconds, zone=config.dashboard.timezone,
            core_url=core_url, local_auto_connect=_is_local_core_url(core_url),
            browser_enabled=bool(getattr(getattr(config, 'browser_automation', None), 'enabled', False)),
            browser_b3_enabled=bool(getattr(getattr(config, 'browser_automation', None), 'b3_enabled', False)))

    @app.get('/api/stats')
    def statistics():
        try:
            if request.args.keys() - {'start', 'end', 'mode'} or any(len(v) != 1 for _, v in request.args.lists()):
                raise ValueError('Unknown or repeated filter')
            today = datetime.now(timezone.utc).astimezone(get_zone(config.dashboard.timezone)).date()
            start = date.fromisoformat(request.args.get('start', (today-timedelta(days=config.dashboard.default_days-1)).isoformat()))
            end = date.fromisoformat(request.args.get('end', today.isoformat()))
            return jsonify(read_stats(config.storage.database, start, end,
                mode=request.args.get('mode', 'all'), zone=config.dashboard.timezone,
                timeout=config.storage.busy_timeout_seconds))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except DashboardError as exc:
            LOG.warning('Dashboard read failed (%s)', type(exc).__name__)
            return jsonify(error=str(exc)), 503

    def activity_history_error(exc):
        if isinstance(exc, ValueError):
            return jsonify(error=str(exc)), 400
        LOG.warning('Activity history request failed (%s)', type(exc).__name__)
        return jsonify(error='Daily reflection is temporarily unavailable.'), 503

    @app.get('/api/activity-history')
    def activity_history():
        if activity_history_store is None:
            return jsonify(error='Daily reflection is temporarily unavailable.'), 503
        try:
            if request.args.keys() - {'date'} or any(len(values) != 1 for _, values in request.args.lists()):
                raise ValueError('Invalid activity-history date')
            zone = get_zone(config.dashboard.timezone)
            today = datetime.now(timezone.utc).astimezone(zone).date()
            selected = date.fromisoformat(request.args.get('date', today.isoformat()))
            first_retained = today - timedelta(days=RETENTION_DAYS - 1)
            if selected > today or selected < first_retained:
                raise ValueError('Choose a date within the last 30 days.')
            start = max(first_retained, selected - timedelta(days=6))
            summaries = activity_history_store.list_summaries(
                start, selected, timezone_name=config.dashboard.timezone,
            )
            return jsonify(
                enabled=activity_history_store.enabled,
                collection_available=config.companion_context.enabled,
                retention_days=RETENTION_DAYS,
                score_enabled=False,
                score=None,
                confirmed_outcomes_available=False,
                confirmed_outcomes_note=(
                    'Core-confirmed mission outcomes are not in this local aggregate endpoint. '
                    'Vision daily reflection may show them when authenticated access is available.'
                ),
                days=[summary.as_dict() for summary in summaries],
            )
        except ValueError as exc:
            return activity_history_error(exc)
        except Exception as exc:
            return activity_history_error(exc)

    @app.put('/api/activity-history/settings')
    def set_activity_history_enabled():
        if activity_history_store is None:
            return jsonify(error='Daily reflection is temporarily unavailable.'), 503
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'enabled'} or type(payload['enabled']) is not bool:
            return jsonify(error='enabled must be a boolean.'), 400
        try:
            if payload['enabled']:
                activity_history_store.purge_expired()
            return jsonify(enabled=activity_history_store.set_enabled(payload['enabled']),
                           retention_days=RETENTION_DAYS)
        except Exception as exc:
            return activity_history_error(exc)

    @app.get('/api/activity-history/export')
    def export_activity_history():
        if activity_history_store is None:
            return jsonify(error='Daily reflection is temporarily unavailable.'), 503
        try:
            summaries = activity_history_store.export(timezone_name=config.dashboard.timezone)
            response = jsonify(summaries=summaries, retention_days=RETENTION_DAYS)
            response.headers['Content-Disposition'] = 'attachment; filename="vision-activity-history.json"'
            return response
        except Exception as exc:
            return activity_history_error(exc)

    @app.delete('/api/activity-history')
    def delete_activity_history():
        if activity_history_store is None:
            return jsonify(error='Daily reflection is temporarily unavailable.'), 503
        try:
            return jsonify(deleted=activity_history_store.delete_all())
        except Exception as exc:
            return activity_history_error(exc)

    @app.post('/api/activity-history/corrections')
    def correct_activity_history():
        if activity_history_store is None:
            return jsonify(error='Daily reflection is temporarily unavailable.'), 503
        payload = request.get_json(silent=True)
        if (not isinstance(payload, dict) or set(payload) != {'date', 'metric', 'remove_minutes'}
                or not isinstance(payload.get('date'), str)
                or payload.get('metric') not in {
                    'focus_session_seconds', 'declared_break_seconds', 'entertainment_category_seconds'
                }
                or isinstance(payload.get('remove_minutes'), bool)
                or not isinstance(payload.get('remove_minutes'), int)
                or not 1 <= payload['remove_minutes'] <= 1440):
            return jsonify(error='Choose a saved day, metric, and 1–1440 minutes to remove.'), 400
        try:
            selected = date.fromisoformat(payload['date'])
            today = datetime.now(timezone.utc).astimezone(get_zone(config.dashboard.timezone)).date()
            if selected > today:
                raise ValueError('A future day cannot be corrected.')
            correction = activity_history_store.correct(
                selected, config.dashboard.timezone, payload['metric'],
                -payload['remove_minutes'] * 60,
            )
            return jsonify(correction={
                'id': correction.correction_id,
                'metric': correction.metric,
                'removed_seconds': correction.removed_seconds,
                'created_at': correction.created_at,
            })
        except Exception as exc:
            return activity_history_error(exc)

    @app.post('/api/chat/messages')
    def enqueue_chat_message():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'text'}:
            return jsonify(error='A message is required.'), 400
        text = payload['text']
        if (not isinstance(text, str) or not text.strip()
                or len(text.strip()) > queue.max_text_length):
            return jsonify(error=f'Enter a message between 1 and {queue.max_text_length} characters.'), 400
        session_id = queue.active_session_id()
        if session_id is None:
            return jsonify(error='Vision is not running; start Vision before sending a chat message.'), 503
        try:
            message_id = queue.enqueue(text, source='dashboard', session_id=session_id)
        except ValueError:
            return jsonify(error='Enter a message between 1 and 4000 characters.'), 400
        except RuntimeError:
            return jsonify(error='Vision is restarting; please send your message again.'), 503
        except Exception as exc:
            LOG.warning('Dashboard chat enqueue failed (%s)', type(exc).__name__)
            return jsonify(error='Vision chat is temporarily unavailable.'), 503
        return jsonify(messageId=message_id), 202

    @app.get('/api/chat/messages')
    def poll_chat_messages():
        try:
            if request.args.keys() - {'after'} or any(len(values) != 1 for _, values in request.args.lists()):
                raise ValueError('Invalid cursor')
            raw_cursor = request.args.get('after', '0')
            if not raw_cursor.isascii() or not raw_cursor.isdecimal():
                raise ValueError('Invalid cursor')
            cursor = int(raw_cursor)
            if cursor > 9_223_372_036_854_775_807:
                raise ValueError('Invalid cursor')
            replies = queue.replies_after(cursor)
        except ValueError:
            return jsonify(error='Cursor must be a non-negative integer.'), 400
        except Exception as exc:
            LOG.warning('Dashboard chat polling failed (%s)', type(exc).__name__)
            return jsonify(error='Vision chat is temporarily unavailable.'), 503

        messages = [{
            'messageId': row['message_id'],
            'text': row['text'],
            'status': row['status'],
            'createdAt': row['created_at'],
        } for row in replies]
        safe_cursor = replies[-1]['cursor'] if replies else cursor
        return jsonify(messages=messages, cursor=safe_cursor)

    @app.get('/api/chat/events')
    def poll_chat_events():
        try:
            if request.args.keys() - {'after'} or any(len(values) != 1 for _, values in request.args.lists()):
                raise ValueError('Invalid cursor')
            raw_cursor = request.args.get('after', '0')
            if not raw_cursor.isascii() or not raw_cursor.isdecimal():
                raise ValueError('Invalid cursor')
            cursor = int(raw_cursor)
            if cursor > 9_223_372_036_854_775_807:
                raise ValueError('Invalid cursor')
            session_id = queue.active_session_id()
            events = queue.events_after(session_id, cursor) if session_id else []
        except ValueError:
            return jsonify(error='Cursor must be a non-negative integer.'), 400
        except Exception as exc:
            LOG.warning('Dashboard chat event poll failed (%s)', type(exc).__name__)
            return jsonify(error='Vision chat is temporarily unavailable.'), 503
        return jsonify(events=events,
                       cursor=events[-1]['cursor'] if events else cursor,
                       sessionId=session_id, aiStatus=queue.ai_status())

    @app.post('/api/chat/ack')
    def acknowledge_chat_replies():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'cursor'}:
            return jsonify(error='A non-negative cursor is required.'), 400
        cursor = payload['cursor']
        if type(cursor) is not int or cursor < 0:
            return jsonify(error='A non-negative cursor is required.'), 400
        try:
            queue.acknowledge_through(cursor)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except Exception as exc:
            LOG.warning('Dashboard chat acknowledgement failed (%s)', type(exc).__name__)
            return jsonify(error='Vision chat is temporarily unavailable.'), 503
        return jsonify(acknowledged=cursor)

    @app.get('/api/memory')
    def list_memory():
        if memory_store is None:
            return jsonify(error='Vision memory is temporarily unavailable.'), 503
        try:
            if request.args.keys() - {'q'} or any(len(values) != 1 for _, values in request.args.lists()):
                raise ValueError('Invalid memory search')
            return jsonify(enabled=memory_store.enabled(), pending=memory_store.pending(),
                           active=memory_store.active(request.args.get('q', '')))
        except Exception as exc:
            return memory_error(exc)

    @app.get('/api/memory/export')
    def export_memory():
        if memory_store is None:
            return jsonify(error='Vision memory is temporarily unavailable.'), 503
        try:
            response = jsonify(memories=memory_store.active(limit=None))
            response.headers['Content-Disposition'] = 'attachment; filename="vision-memories.json"'
            return response
        except Exception as exc:
            return memory_error(exc)

    @app.post('/api/memory/proposals/<int:proposal_id>/approve')
    def approve_memory(proposal_id):
        if memory_store is None:
            return jsonify(error='Vision memory is temporarily unavailable.'), 503
        try:
            return jsonify(memory=memory_store.approve(proposal_id))
        except Exception as exc:
            return memory_error(exc)

    @app.post('/api/memory/proposals/<int:proposal_id>/reject')
    def reject_memory(proposal_id):
        if memory_store is None:
            return jsonify(error='Vision memory is temporarily unavailable.'), 503
        try:
            return jsonify(rejected=memory_store.reject(proposal_id))
        except Exception as exc:
            return memory_error(exc)

    @app.patch('/api/memory/<int:memory_id>')
    def edit_memory(memory_id):
        if memory_store is None:
            return jsonify(error='Vision memory is temporarily unavailable.'), 503
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'text'}:
            return jsonify(error='Enter one memory text.'), 400
        try:
            return jsonify(memory=memory_store.edit(memory_id, payload['text']))
        except Exception as exc:
            return memory_error(exc)

    @app.delete('/api/memory/<int:memory_id>')
    def delete_memory(memory_id):
        if memory_store is None:
            return jsonify(error='Vision memory is temporarily unavailable.'), 503
        try:
            return jsonify(deleted=memory_store.delete(memory_id))
        except Exception as exc:
            return memory_error(exc)

    @app.put('/api/memory/settings')
    def set_memory_enabled():
        if memory_store is None:
            return jsonify(error='Vision memory is temporarily unavailable.'), 503
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'enabled'} or type(payload['enabled']) is not bool:
            return jsonify(error='enabled must be a boolean.'), 400
        try:
            memory_store.set_enabled(payload['enabled'])
            return jsonify(enabled=memory_store.enabled())
        except Exception as exc:
            return memory_error(exc)

    @app.get('/api/auth/local-vision-status')
    def local_vision_status():
        """Return safe local authorization status without exposing the Vision token."""
        token_store = VisionTokenStore()
        context_store = VisionContextStore()
        token = token_store.load()
        context = context_store.load()
        if token is None or context is None:
            _clear_handoff_stores(token_store, context_store)
            return jsonify(status='re-authentication-required', coreApi='unreachable')

        core_api = 'unreachable'
        base_url = os.getenv(config.ascend.base_url_env, '').strip()
        if _is_local_core_url(base_url):
            try:
                result = AscendClient(base_url, timeout_seconds=config.ascend.timeout_seconds,
                                      token_store=token_store).get_automation_capabilities()
                if result.state is AscendConnectionState.CONNECTED:
                    core_api = 'reachable'
            except Exception as exc:
                LOG.warning('Local Core API status check failed (%s)', type(exc).__name__)
        return jsonify(
            status='connected',
            character={'id': context.character_id, 'name': context.character_name},
            expiresAt=context.expires_at.isoformat(),
            coreApi=core_api,
        )

    @app.post('/api/auth/vision-handoff')
    def vision_handoff():
        """Loopback-only sign-in exchange; passwords and web tokens are never persisted or logged."""
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'identifier', 'password'}:
            return jsonify(error='Invalid sign-in request.'), 400
        identifier, password = payload['identifier'], payload['password']
        if not isinstance(identifier, str) or not identifier.strip() or not isinstance(password, str) or not password:
            return jsonify(error='Sign-in details are required.'), 400
        base_url = os.getenv(config.ascend.base_url_env, '').strip()
        if not base_url:
            return jsonify(error='Core connection is not configured.'), 503
        client = AscendClient(base_url, timeout_seconds=config.ascend.timeout_seconds)
        result = client.login_and_obtain_vision_token(identifier, password)
        if result.state is not AscendConnectionState.CONNECTED:
            LOG.warning('Vision sign-in handoff failed: %s', result.state.value)
            return jsonify(error='Core sign-in could not be completed.'), 401
        return jsonify(status='connected')

    @app.post('/api/auth/local-vision-handoff')
    def local_vision_handoff():
        """Persist a browser-handoff token only on the loopback-bound local dashboard."""
        base_url = os.getenv(config.ascend.base_url_env, '').strip()
        if not _is_local_core_url(base_url) or not _is_loopback_host(request.host):
            abort(403)

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'accessToken', 'expiresAt', 'character'}:
            return jsonify(error='Invalid local Vision handoff.'), 400

        token = payload['accessToken']
        expires_at = _parse_future_expiry(payload['expiresAt'])
        character = payload['character']
        if (not isinstance(token, str) or not token.strip() or expires_at is None
                or not isinstance(character, dict) or set(character) != {'id', 'name'}
                or not isinstance(character['id'], str) or not character['id'].strip()
                or not isinstance(character['name'], str) or not character['name'].strip()):
            return jsonify(error='Invalid local Vision handoff.'), 400

        vision_token = VisionToken(access_token=token, expires_at=expires_at)
        vision_context = VisionAuthContext(
            character_id=character['id'].strip(),
            character_name=character['name'].strip(),
            expires_at=expires_at,
        )
        token_store = VisionTokenStore()
        context_store = VisionContextStore()
        try:
            token_store.save(vision_token)
            context_store.save(vision_context)
        except Exception:
            _clear_handoff_stores(token_store, context_store)
            LOG.warning('Local Vision handoff storage failed')
            return jsonify(error='Vision authorization could not be saved.'), 503

        return jsonify(
            status='connected',
            character={'id': vision_context.character_id, 'name': vision_context.character_name},
            expiresAt=vision_context.expires_at.astimezone(timezone.utc).isoformat(),
        )

    @app.get('/api/camera/status')
    def camera_status():
        import psutil
        running = False
        for p in psutil.process_iter(['name', 'cmdline']):
            try:
                cmd = ' '.join(p.info.get('cmdline') or [])
                if 'main.py' in cmd and not cmd.endswith('dashboard.py'):
                    running = True
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return jsonify(running=running)

    @app.post('/api/camera/start')
    def start_camera():
        import subprocess
        import sys
        # Launch main.py in a separate interactive window on Windows
        base_dir = Path(__file__).resolve().parent
        python_exe = sys.executable
        try:
            subprocess.Popen(
                ['cmd.exe', '/c', 'start', 'Phone Watch - Live Camera', python_exe, 'main.py'],
                cwd=str(base_dir),
                creationflags=subprocess.CREATE_NEW_CONSOLE if hasattr(subprocess, 'CREATE_NEW_CONSOLE') else 0
            )
            return jsonify(status='started')
        except Exception as exc:
            LOG.error('Failed to start camera: %s', exc)
            return jsonify(error=str(exc)), 500

    browser_config = getattr(config, 'browser_automation', None)
    browser_file_store = None
    if browser_config is not None and browser_config.b3_enabled:
        try:
            from browser.files import BrowserTaskFiles
            browser_file_store = BrowserTaskFiles()
        except Exception as exc:
            LOG.info('Browser file handoff unavailable (%s)', type(exc).__name__)
    if browser_config is not None and browser_config.enabled and browser_client is None:
        try:
            from browser.ipc import BrowserIpcClient
            browser_client = BrowserIpcClient.from_keyring
        except Exception as exc:
            LOG.info('Dashboard browser broker unavailable (%s)', type(exc).__name__)
    if browser_config is not None:
        from browser.dashboard_routes import register_browser_routes
        register_browser_routes(app, browser_client, browser_config, file_store=browser_file_store)

    return app


def _is_loopback_host(host: str) -> bool:
    return urlsplit(f'//{host}').hostname in {'localhost', '127.0.0.1', '::1'}


def _is_local_core_url(base_url: str) -> bool:
    parsed = urlsplit(base_url)
    return parsed.scheme in {'http', 'https'} and parsed.hostname in {'localhost', '127.0.0.1', '::1'}


def _parse_future_expiry(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        expires_at = datetime.fromisoformat(value)
    except ValueError:
        return None
    if expires_at.tzinfo is None or expires_at <= datetime.now(timezone.utc):
        return None
    return expires_at.astimezone(timezone.utc)


def _clear_handoff_stores(token_store: VisionTokenStore, context_store: VisionContextStore) -> None:
    """Best-effort cleanup must not expose a credential-store failure to the caller."""
    for store in (token_store, context_store):
        try:
            store.clear()
        except Exception:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description='Phone Watch habit dashboard (Phase 5)')
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.yaml'))
    parser.add_argument('--env-file', type=Path,
                        help='load provider credentials from this owner-managed env file')
    parser.add_argument('--port', type=int, help='override loopback HTTP port')
    parser.add_argument('--open-browser', action=argparse.BooleanOptionalAction, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    server = None
    try:
        from assistant.runtime_environment import load_runtime_environment
        load_runtime_environment(args.config, args.env_file)
        config = load_config(args.config)
        options = config.dashboard
        if args.port is not None:
            options = replace(options, port=args.port)
        if args.open_browser is not None:
            options = replace(options, open_browser=args.open_browser)
        config = replace(config, dashboard=options)
        from waitress import create_server
        # This server must remain loopback-only: the handoff endpoint persists a Vision credential.
        server = create_server(create_app(config), host='127.0.0.1', port=options.port, threads=4)
        # Use localhost so Core's host-only localhost session cookie is sent by the browser.
        url = f'http://localhost:{options.port}'
        LOG.info('Dashboard: %s — Ctrl+C to stop. Detection runs separately.', url)
        if options.open_browser:
            try:
                webbrowser.open(url)
            except webbrowser.Error:
                LOG.warning('Could not open a browser; use the printed dashboard URL')
        server.run()
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as exc:
        LOG.error('Dashboard startup failed: %s', exc)
        return 1
    finally:
        if server is not None:
            server.close()


if __name__ == '__main__':
    raise SystemExit(main())
