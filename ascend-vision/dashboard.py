"""Independent local dashboard: no detector, camera, API key or writer startup."""
import argparse
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit
import webbrowser

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, render_template, request

from config import load_config
from dashboard_stats import DashboardError, get_zone, read_stats
from integrations.ascend_client import AscendClient, AscendConnectionState
from integrations.chat_ipc import ChatIpcQueue
from integrations.vision_context import VisionAuthContext, VisionContextStore
from integrations.vision_token_store import VisionToken, VisionTokenStore

LOG = logging.getLogger(__name__)


def create_app(config, chat_queue=None):
    app = Flask(__name__)
    app.config.update(TRUSTED_HOSTS=['127.0.0.1', 'localhost'])
    queue = chat_queue if chat_queue is not None else ChatIpcQueue(
        Path(config.storage.database).parent / 'chat_ipc.db')

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify(error='Request is too large.'), 400

    @app.before_request
    def local_requests_only():
        if request.content_length is not None:
            max_bytes = 20_000 if request.path.startswith('/api/chat/') else 1_024
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
            core_url=core_url, local_auto_connect=_is_local_core_url(core_url))

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

    @app.post('/api/chat/messages')
    def enqueue_chat_message():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'text'}:
            return jsonify(error='A message is required.'), 400
        try:
            message_id = queue.enqueue(payload['text'], source='dashboard')
        except ValueError:
            return jsonify(error='Enter a message between 1 and 4000 characters.'), 400
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
    parser.add_argument('--port', type=int, help='override loopback HTTP port')
    parser.add_argument('--open-browser', action=argparse.BooleanOptionalAction, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    server = None
    try:
        load_dotenv(args.config.parent / '.env', override=False)
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
