"""Independent local dashboard: no detector, camera, API key or writer startup."""
import argparse
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import webbrowser

from flask import Flask, abort, jsonify, render_template, request

from config import load_config
from dashboard_stats import DashboardError, get_zone, read_stats
from integrations.ascend_client import AscendClient, AscendConnectionState

LOG = logging.getLogger(__name__)


def create_app(config):
    app = Flask(__name__)
    app.config.update(TRUSTED_HOSTS=['127.0.0.1', 'localhost'], MAX_CONTENT_LENGTH=1024)

    @app.before_request
    def local_requests_only():
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
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
            "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    @app.get('/')
    def index():
        today = datetime.now(timezone.utc).astimezone(get_zone(config.dashboard.timezone)).date()
        return render_template('dashboard.html', today=today.isoformat(),
            start=(today-timedelta(days=config.dashboard.default_days-1)).isoformat(),
            refresh=config.dashboard.refresh_seconds, zone=config.dashboard.timezone)

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


def main(argv=None):
    parser = argparse.ArgumentParser(description='Phone Watch habit dashboard (Phase 5)')
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.yaml'))
    parser.add_argument('--port', type=int, help='override loopback HTTP port')
    parser.add_argument('--open-browser', action=argparse.BooleanOptionalAction, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    server = None
    try:
        config = load_config(args.config)
        options = config.dashboard
        if args.port is not None:
            options = replace(options, port=args.port)
        if args.open_browser is not None:
            options = replace(options, open_browser=args.open_browser)
        config = replace(config, dashboard=options)
        from waitress import create_server
        server = create_server(create_app(config), host='127.0.0.1', port=options.port, threads=4)
        url = f'http://127.0.0.1:{options.port}'
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
