"""Local Fairy UI bridge. CameraCapture remains the sole owner of the device."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
import hmac
import os
from pathlib import Path
import queue
import threading
import time
import webbrowser

from flask import Flask, Response, abort, jsonify, request, send_from_directory
from werkzeug.serving import make_server

from assistant.runtime_health import RuntimeHealth
from browser.dashboard_routes import register_browser_routes
from dashboard_stats import DashboardError, get_zone, read_stats

LOG = logging.getLogger(__name__)
FAIRY_BROWSER_SESSION = ('local', 'dashboard', 'local-fairy')


class _LiveBrowserConfig:
    """Routes are registered before the browser service exists; read settings when used."""
    enabled = True

    def __init__(self, owner):
        self._owner = owner

    def __getattr__(self, name):
        return getattr(self._owner._browser_config, name)
BUILD_DIR = Path(__file__).with_name('fairy-ui') / 'dist'


class FocusUI:
    """Bounded in-memory frame/state mailbox; HTTP never accesses the camera."""

    def __init__(self, *, build_dir=BUILD_DIR, health=None, launch_token=None):
        self.build_dir = Path(build_dir)
        self.health = health or RuntimeHealth()
        self._launch_token = os.environ.get('ASCEND_LAUNCH_TOKEN', '') if launch_token is None else launch_token
        self.shutdown_requested = threading.Event()
        self._lock = threading.Lock()
        self._frame = None
        self._state = {'mode': 'focus', 'cameraReady': False, 'audioLevel': 0,
                       'voiceEnabled': False, 'muted': False, 'speaking': False,
                       'elapsedSeconds': 0, 'faceTarget': None, 'phoneBox': None,
                       'hands': [], 'eyePoints': [], 'lipPoints': [],
                       'posture': None, 'fatigue': None, 'gesture': None,
                       'emotion': 'neutral', 'lastHeard': '',
                       'webUse': {'enabled': False, 'writes': False},
                       'activePanel': None, 'activePanelSequence': 0, 'speakReplies': False,
                       'coreConnection': {'configured': False, 'state': 'unconfigured',
                                          'lastCheckedAt': None},
                       'aiStatus': {'configured': False, 'state': 'unknown', 'provider': None,
                                    'model': None, 'lastSuccessAt': None, 'lastFailure': None}}
        self._preview_until = 0.0
        self.commands: queue.Queue[str] = queue.Queue(maxsize=16)
        self._chat_queue = None
        self._runtime_session_id = None
        self._config = None
        self._memory = None
        self._browser_client = None
        self._browser_config = None
        self._key_status = None
        self._away_state = None
        self._nutrition = None
        self._key_cache = (0.0, None)
        self._server = None
        self._thread = None
        self.app = self._create_app()

    def _create_app(self):
        app = Flask(__name__, static_folder=None)
        app.config.update(TRUSTED_HOSTS=['127.0.0.1', 'localhost'], MAX_CONTENT_LENGTH=1024)

        @app.before_request
        def local_only():
            request.max_content_length = (
                20_000 if request.path in ('/api/fairy/chat', '/api/browser/tasks') else 1_024)
            if request.path.startswith('/api/browser/') and self._browser_config is None:
                return jsonify(error='Web use is turned off. Run Setup.bat to turn it on.'), 503
            origin = request.headers.get('Origin')
            if request.headers.get('Sec-Fetch-Site') == 'cross-site' or (origin and origin != request.host_url.rstrip('/')):
                abort(403)

        @app.after_request
        def private(response):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['Referrer-Policy'] = 'no-referrer'
            response.headers['Content-Security-Policy'] = (
                "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' blob:; media-src 'self' blob:; connect-src 'self'; "
                "object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            return response

        @app.get('/')
        def index():
            return send_from_directory(self.build_dir, 'index.html')

        @app.get('/assets/<path:filename>')
        def assets(filename):
            return send_from_directory(self.build_dir / 'assets', filename)

        @app.get('/vision/<path:filename>')
        def vision_assets(filename):
            return send_from_directory(self.build_dir / 'vision', filename)

        @app.get('/api/fairy/state')
        def state():
            keys = self._key_summary()
            with self._lock:
                snapshot = dict(self._state)
            if keys is not None:
                snapshot['aiKeys'] = keys
            if self._away_state is not None:
                try:
                    snapshot['awayState'] = self._away_state()
                except Exception:
                    pass
            return jsonify(snapshot)

        @app.get('/api/fairy/health')
        def health():
            # The supervisor passes the boot secret through the child environment;
            # it never appears in URLs, stdout, browser state, or the response.
            if self._launch_token and not hmac.compare_digest(
                    request.headers.get('Authorization', ''), 'Bearer ' + self._launch_token):
                abort(403)
            return jsonify(self.health.snapshot())

        @app.post('/api/fairy/shutdown')
        def owner_shutdown():
            if not self._launch_token or not hmac.compare_digest(
                    request.headers.get('Authorization', ''), 'Bearer ' + self._launch_token):
                abort(403)
            self.shutdown_requested.set()
            return jsonify(stopping=True), 202

        @app.get('/api/fairy/frame.jpg')
        def frame():
            with self._lock:
                self._preview_until = time.monotonic() + 2
                image = self._frame
            if image is None:
                return Response(status=204)
            import cv2
            ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if not ok:
                return Response(status=503)
            return Response(encoded.tobytes(), mimetype='image/jpeg')

        @app.post('/api/fairy/command')
        def command():
            with self._lock:
                recovery = self._state.get('runtimeMode') == 'recovery-chat'
            if recovery:
                return jsonify(error='Hardware and automation controls are unavailable in recovery chat.'), 409
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or set(payload) != {'command'}:
                return jsonify(error='A command is required.'), 400
            value = payload['command']
            if value not in ('toggle-focus', 'toggle-voice', 'toggle-speech',
                             'toggle-chat-speech', 'stop-cancel'):
                return jsonify(error='Unknown command.'), 400
            try:
                self.commands.put_nowait(value)
            except queue.Full:
                return jsonify(error='Please wait for the previous command.'), 429
            return jsonify(queued=True), 202

        @app.post('/api/fairy/chat')
        def submit_chat():
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or set(payload) != {'text'}:
                return jsonify(error='A message is required.'), 400
            if self._chat_queue is None or self._runtime_session_id is None:
                return jsonify(error='Vision chat is not ready.'), 503
            try:
                if self._chat_queue.active_session_id() != self._runtime_session_id:
                    return jsonify(error='Vision is restarting; please send your message again.'), 503
                message_id = self._chat_queue.enqueue(
                    payload['text'], source='fairy', session_id=self._runtime_session_id,
                )
            except ValueError:
                return jsonify(error='Enter a message between 1 and 4000 characters.'), 400
            except RuntimeError:
                return jsonify(error='Vision is restarting; please send your message again.'), 503
            except Exception:
                LOG.exception('Fairy chat enqueue failed')
                return jsonify(error='Vision chat is temporarily unavailable.'), 503
            return jsonify(messageId=message_id), 202

        @app.get('/api/fairy/chat/events')
        def chat_events():
            if request.args.keys() - {'after'} or any(
                    len(values) != 1 for _, values in request.args.lists()):
                return jsonify(error='Invalid chat event cursor.'), 400
            raw_cursor = request.args.get('after', '0')
            if not raw_cursor.isascii() or not raw_cursor.isdecimal():
                return jsonify(error='Cursor must be a non-negative integer.'), 400
            cursor = int(raw_cursor)
            if cursor > 9_223_372_036_854_775_807:
                return jsonify(error='Cursor must be a non-negative integer.'), 400
            if self._chat_queue is None or self._runtime_session_id is None:
                return jsonify(events=[], cursor=cursor, sessionId=None)
            try:
                events = self._chat_queue.events_after(self._runtime_session_id, cursor)
            except Exception:
                LOG.exception('Fairy chat event poll failed')
                return jsonify(error='Vision chat is temporarily unavailable.'), 503
            return jsonify(events=events,
                           cursor=events[-1]['cursor'] if events else cursor,
                           sessionId=self._runtime_session_id)

        def memory_unavailable():
            return jsonify(error='Vision memory is temporarily unavailable.'), 503

        @app.get('/api/fairy/stats')
        def stats():
            if self._config is None:
                return jsonify(error='Statistics are not ready yet.'), 503
            if request.args.keys() - {'days'}:
                return jsonify(error='Unknown filter.'), 400
            raw = request.args.get('days', '7')
            if raw not in ('7', '28'):
                return jsonify(error='Choose 7 or 28 days.'), 400
            zone = self._config.dashboard.timezone
            today = datetime.now(timezone.utc).astimezone(get_zone(zone)).date()
            start = today - timedelta(days=int(raw) - 1)
            try:
                result = read_stats(self._config.storage.database, start, today,
                                    zone=zone, timeout=self._config.storage.busy_timeout_seconds)
                try:
                    from integrations.distraction_watch import DistractionStore
                    result['distractions'] = DistractionStore.totals(
                        Path(self._config.storage.database).parent / 'distractions.db', start, today)
                except Exception as exc:
                    LOG.warning('Distraction totals unavailable (%s)', type(exc).__name__)
                    result['distractions'] = None
                return jsonify(result)
            except DashboardError as exc:
                LOG.warning('Fairy stats read failed (%s)', type(exc).__name__)
                return jsonify(error=str(exc)), 503

        @app.get('/api/fairy/memory')
        def memory_list():
            if self._memory is None:
                return memory_unavailable()
            try:
                return jsonify(enabled=self._memory.enabled(), pending=self._memory.pending(),
                               active=self._memory.active(''))
            except Exception as exc:
                LOG.warning('Fairy memory read failed (%s)', type(exc).__name__)
                return memory_unavailable()

        def memory_action(action):
            if self._memory is None:
                return memory_unavailable()
            try:
                return action()
            except ValueError as exc:
                return jsonify(error=str(exc)), 400
            except Exception as exc:
                LOG.warning('Fairy memory request failed (%s)', type(exc).__name__)
                return memory_unavailable()

        @app.post('/api/fairy/memory/proposals/<int:proposal_id>/approve')
        def memory_approve(proposal_id):
            return memory_action(lambda: jsonify(memory=self._memory.approve(proposal_id)))

        @app.post('/api/fairy/memory/proposals/<int:proposal_id>/reject')
        def memory_reject(proposal_id):
            return memory_action(lambda: jsonify(rejected=self._memory.reject(proposal_id)))

        @app.delete('/api/fairy/memory/<int:memory_id>')
        def memory_delete(memory_id):
            return memory_action(lambda: jsonify(deleted=self._memory.delete(memory_id)))

        @app.put('/api/fairy/memory/settings')
        def memory_settings():
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or set(payload) != {'enabled'} or type(payload['enabled']) is not bool:
                return jsonify(error='enabled must be a boolean.'), 400

            def apply():
                self._memory.set_enabled(payload['enabled'])
                return jsonify(enabled=self._memory.enabled())
            return memory_action(apply)

        def food_unavailable():
            return jsonify(error='The food diary is not available right now.'), 503

        @app.get('/api/fairy/food')
        def food_today():
            if self._nutrition is None:
                return food_unavailable()
            from assistant.nutrition import GLASS_ML, calorie_target, meal_plan, water_target_ml
            store, coach = self._nutrition
            today = datetime.now().date()
            profile = store.profile()
            entries = store.foods(today)
            body = {'ready': profile is not None, 'eaten': sum(item['kcal'] for item in entries),
                    'entries': entries, 'waterMl': store.water_ml(today), 'glassMl': GLASS_ML,
                    'target': None, 'waterTargetMl': None, 'meals': []}
            if profile is not None:
                try:
                    hot = bool(coach.hot_day())
                except Exception:
                    hot = False
                body.update(target=calorie_target(profile), waterTargetMl=water_target_ml(profile, hot_day=hot),
                            meals=[{'name': name, 'at': when.strftime('%H:%M')}
                                   for name, when in meal_plan(profile, today)])
            return jsonify(body)

        @app.patch('/api/fairy/food/<int:entry_id>')
        def food_fix(entry_id):
            if self._nutrition is None:
                return food_unavailable()
            payload = request.get_json(silent=True)
            if (not isinstance(payload, dict) or set(payload) != {'kcal'} or type(payload['kcal']) is not int
                    or not 0 <= payload['kcal'] <= 5000):
                return jsonify(error='Enter calories between 0 and 5000.'), 400
            return jsonify(updated=self._nutrition[0].update_food(entry_id, payload['kcal']))

        @app.delete('/api/fairy/food/<int:entry_id>')
        def food_delete(entry_id):
            if self._nutrition is None:
                return food_unavailable()
            return jsonify(deleted=self._nutrition[0].delete_food(entry_id))

        @app.post('/api/fairy/water')
        def water_add():
            if self._nutrition is None:
                return food_unavailable()
            payload = request.get_json(silent=True)
            if (not isinstance(payload, dict) or set(payload) != {'ml'} or type(payload['ml']) is not int
                    or not 50 <= payload['ml'] <= 2000):
                return jsonify(error='Enter between 50 and 2000 ml.'), 400
            self._nutrition[0].add_water(payload['ml'], datetime.now())
            return jsonify(added=payload['ml'])

        register_browser_routes(app, lambda: self._browser_client, _LiveBrowserConfig(self),
                                session_key=FAIRY_BROWSER_SESSION)
        return app

    def bind_nutrition(self, store, coach):
        """The Food tab reads and edits the local food and water diary."""
        self._nutrition = (store, coach)

    def bind_away(self, state_provider):
        """'active', 'asking' or 'sleeping', shown as a label in the window."""
        self._away_state = state_provider

    def bind_key_status(self, summary_provider):
        """A callable returning per-provider key counts; read at most every few seconds."""
        self._key_status = summary_provider

    def _key_summary(self):
        if self._key_status is None:
            return None
        fetched_at, cached = self._key_cache
        if time.monotonic() - fetched_at < 5:
            return cached
        try:
            cached = self._key_status()
        except Exception:
            cached = None
        self._key_cache = (time.monotonic(), cached)
        return cached

    def bind_browser(self, client, browser_config):
        """Expose the in-process browser service to the one Vision window."""
        self._browser_client = client
        self._browser_config = browser_config
        with self._lock:
            self._state['webUse'] = {'enabled': client is not None,
                                     'writes': bool(client is not None and browser_config.b3_enabled)}

    def bind_data(self, config, memory_store):
        """Give the single UI read access to saved statistics and memory approvals."""
        self._config = config
        self._memory = memory_store

    def bind_chat(self, chat_queue, runtime_session_id: str):
        """Connect the UI bridge to the active runtime-owned local conversation."""
        self._chat_queue = chat_queue
        self._runtime_session_id = runtime_session_id

    def mark_chat_ready(self):
        if self._chat_queue is None or self._runtime_session_id is None:
            raise RuntimeError('Bind the chat transport before marking it ready')
        self.health.update(chat='ready')

    def publish_recovery_mode(self):
        self.health.update(camera='unavailable', microphone='unavailable')
        with self._lock:
            self._state['runtimeMode'] = 'recovery-chat'
            self._state['runtimeNotice'] = (
                'Recovery chat: camera, microphone, speech, focus sessions and automations '
                'are unavailable. Stop this mode before restarting full Vision.')

    def publish_core_connection(self, *, configured: bool, state: str,
                                 last_checked_at: str | None = None):
        if type(configured) is not bool or state not in {
                'unconfigured', 'connecting', 'connected', 'reauth-required',
                'offline', 'stale'}:
            raise ValueError('invalid Core connection state')
        self.health.update(core=state)
        with self._lock:
            self._state['coreConnection'] = {
                'configured': configured,
                'state': state,
                'lastCheckedAt': last_checked_at,
            }

    def publish_ai_status(self, status: dict):
        if not isinstance(status, dict):
            raise ValueError('AI status must be a mapping')
        allowed_states = {'unknown', 'not-configured', 'configured-untested', 'requesting', 'available', 'request-failed'}
        if status.get('state') not in allowed_states or type(status.get('configured')) is not bool:
            raise ValueError('invalid AI status')
        safe = {key: status.get(key) for key in (
            'configured', 'state', 'provider', 'model', 'lastSuccessAt', 'lastFailure',
        )}
        for key in ('provider', 'model', 'lastFailure', 'lastSuccessAt'):
            value = safe[key]
            if value is not None and (not isinstance(value, str) or len(value) > 80):
                raise ValueError('invalid AI status field')
        self.health.update(ai=safe['state'])
        with self._lock:
            self._state['aiStatus'] = safe

    def toggle_speak_replies(self) -> bool:
        with self._lock:
            self._state['speakReplies'] = not self._state['speakReplies']
            return self._state['speakReplies']

    def should_speak_replies(self) -> bool:
        with self._lock:
            return bool(self._state['speakReplies'])

    # Assistant mode runs without a camera; the window and the launcher then show it as off.
    camera_enabled = True

    def publish(self, image, **state):
        """Called from the vision loop. No encoding/network I/O on that thread."""
        self.health.update(camera='ready' if self.camera_enabled else 'disabled')
        with self._lock:
            self._state.update(state, cameraReady=self.camera_enabled)
            # Only retain an extra image while a preview client is requesting frames.
            keep = self.camera_enabled and image is not None and time.monotonic() < self._preview_until
            self._frame = image.copy() if keep else None

    def publish_face(self, landmarks, width, height):
        """Normalized mirrored face position only; no landmarks/images leave this method."""
        import math
        points = [(float(point[0]), float(point[1])) for point in landmarks
                  if len(point) >= 2 and math.isfinite(point[0]) and math.isfinite(point[1])]
        target = None
        if points and width > 0 and height > 0:
            xs, ys = zip(*points)
            x, y = (min(xs) + max(xs)) / (2 * width), (min(ys) + max(ys)) / (2 * height)
            target = {'x': max(-1.8, min(1.8, (1 - x * 2) * 1.8)),
                      'y': max(-1.8, min(1.8, (y * 2 - 1) * 1.8))}
        with self._lock:
            self._state['faceTarget'] = target

    def publish_telemetry(self, *, phone_box=None, hands=None, face_landmarks=None,
                          posture=None, fatigue=None, gesture=None, active_panel=None,
                          active_panel_sequence=None,
                          emotion=None,
                          last_heard=None, width=640, height=480):
        """Serialize lightweight normalized detection telemetry for client-side HUD."""
        with self._lock:
            if phone_box is not None:
                x1, y1, x2, y2 = phone_box.xyxy
                self._state['phoneBox'] = {
                    'x1': round(float(x1) / max(1, width), 4),
                    'y1': round(float(y1) / max(1, height), 4),
                    'x2': round(float(x2) / max(1, width), 4),
                    'y2': round(float(y2) / max(1, height), 4),
                    'confidence': round(float(phone_box.confidence), 3),
                    'posture': getattr(phone_box, 'posture', None),
                }
            else:
                self._state['phoneBox'] = None

            if hands:
                self._state['hands'] = [{
                    'points': [[round(float(p[0]) / max(1, width), 3),
                                round(float(p[1]) / max(1, height), 3)]
                               for p in hand if len(p) >= 2],
                } for hand in hands]
            else:
                self._state['hands'] = []

            if face_landmarks and width > 0 and height > 0:
                eye_indices = (33, 160, 158, 133, 153, 144, 362, 385, 387, 263, 373, 380)
                lip_indices = (61, 37, 0, 267, 291, 314, 17, 84)
                self._state['eyePoints'] = [
                    [round(float(face_landmarks[idx][0]) / width, 3),
                     round(float(face_landmarks[idx][1]) / height, 3)]
                    for idx in eye_indices if idx < len(face_landmarks)
                ]
                self._state['lipPoints'] = [
                    [round(float(face_landmarks[idx][0]) / width, 3),
                     round(float(face_landmarks[idx][1]) / height, 3)]
                    for idx in lip_indices if idx < len(face_landmarks)
                ]
            else:
                self._state['eyePoints'] = []
                self._state['lipPoints'] = []

            if posture is not None:
                self._state['posture'] = posture
            if fatigue is not None:
                self._state['fatigue'] = fatigue
            if gesture is not None:
                self._state['gesture'] = gesture
            if active_panel is not None:
                self._state['activePanel'] = active_panel
            if type(active_panel_sequence) is int and active_panel_sequence >= 0:
                self._state['activePanelSequence'] = active_panel_sequence
            if emotion is not None:
                self._state['emotion'] = emotion
            if last_heard is not None:
                self._state['lastHeard'] = last_heard

    def start(self, *, open_browser=True):
        if not (self.build_dir / 'index.html').is_file():
            raise RuntimeError('Build Fairy UI first: cd fairy-ui && npm install && npm run build')
        self._server = make_server('127.0.0.1', 0, self.app, threaded=True)
        self._thread = threading.Thread(target=self._server.serve_forever, name='fairy-ui', daemon=True)
        self._thread.start()
        self.health.update(ui='ready')
        url = f'http://127.0.0.1:{self._server.server_port}/?runtime=1'
        print(f'ASCEND_FAIRY_URL={url}', flush=True)
        LOG.info('Fairy focus UI: %s', url)
        if open_browser and os.environ.get('ASCEND_OPEN_BROWSER') != '0':
            try:
                webbrowser.open(url)
            except Exception as exc:
                LOG.warning('Could not open Fairy automatically (%s); open the URL above.', type(exc).__name__)
        return url

    def close(self):
        self.health.fail('host_stopping')
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        with self._lock:
            self._frame = None

    def drain_commands(self):
        while True:
            try:
                yield self.commands.get_nowait()
            except queue.Empty:
                return
