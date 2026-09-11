"""Local Fairy UI bridge. CameraCapture remains the sole owner of the device."""
from __future__ import annotations

import logging
from pathlib import Path
import queue
import threading
import time
import webbrowser

from flask import Flask, Response, abort, jsonify, request, send_from_directory
from werkzeug.serving import make_server

LOG = logging.getLogger(__name__)
BUILD_DIR = Path(__file__).with_name('fairy-ui') / 'dist'


class FocusUI:
    """Bounded in-memory frame/state mailbox; HTTP never accesses the camera."""

    def __init__(self, *, build_dir=BUILD_DIR):
        self.build_dir = Path(build_dir)
        self._lock = threading.Lock()
        self._frame = None
        self._state = {'mode': 'focus', 'cameraReady': False, 'audioLevel': 0,
                       'voiceEnabled': False, 'muted': False, 'speaking': False,
                       'elapsedSeconds': 0, 'faceTarget': None, 'phoneBox': None,
                       'hands': [], 'eyePoints': [], 'lipPoints': [],
                       'posture': None, 'fatigue': None, 'gesture': None,
                       'emotion': 'neutral', 'lastHeard': ''}
        self._preview_until = 0.0
        self.commands: queue.Queue[str] = queue.Queue(maxsize=16)
        self._server = None
        self._thread = None
        self.app = self._create_app()

    def _create_app(self):
        app = Flask(__name__, static_folder=None)
        app.config.update(TRUSTED_HOSTS=['127.0.0.1', 'localhost'], MAX_CONTENT_LENGTH=1024)

        @app.before_request
        def local_only():
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
            with self._lock:
                return jsonify(dict(self._state))

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
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or set(payload) != {'command'}:
                return jsonify(error='A command is required.'), 400
            value = payload['command']
            if value not in ('toggle-focus', 'toggle-voice'):
                return jsonify(error='Unknown command.'), 400
            try:
                self.commands.put_nowait(value)
            except queue.Full:
                return jsonify(error='Please wait for the previous command.'), 429
            return jsonify(queued=True), 202

        return app

    def publish(self, image, **state):
        """Called from the vision loop. No encoding/network I/O on that thread."""
        with self._lock:
            self._state.update(state, cameraReady=True)
            # Only retain an extra image while a preview client is requesting frames.
            self._frame = image.copy() if time.monotonic() < self._preview_until else None

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
                          posture=None, fatigue=None, gesture=None, emotion=None,
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
        url = f'http://127.0.0.1:{self._server.server_port}/?runtime=1'
        LOG.info('Fairy focus UI: %s', url)
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception as exc:
                LOG.warning('Could not open Fairy automatically (%s); open the URL above.', type(exc).__name__)
        return url

    def close(self):
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
