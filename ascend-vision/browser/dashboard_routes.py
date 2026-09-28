"""Authenticated-local-dashboard browser task submission and control routes."""

import time
import uuid

from flask import abort, jsonify, request

from browser.contracts import BrowserTaskRequest
from browser.service import TaskNotFound, TaskQueueFull


DASHBOARD_SESSION_KEY = ('local', 'dashboard', 'local-dashboard')


def register_browser_routes(app, client, config) -> None:
    if not config.enabled:
        return

    def browser_client():
        if client is None:
            return None
        if callable(client) and not hasattr(client, 'submit'):
            return client()
        return client

    @app.post('/api/browser/tasks')
    def submit_browser_task():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'goal'}:
            return jsonify(error='Provide one browser research goal.'), 400
        try:
            task = BrowserTaskRequest.from_payload({
                'schema_version': 1,
                'task_id': uuid.uuid4().hex,
                'session_key': list(DASHBOARD_SESSION_KEY),
                'goal': payload['goal'],
                'provider': config.provider,
                'scope_mode': config.scope_mode,
                'expires_at': time.time() + config.task_timeout_seconds,
            })
        except (TypeError, ValueError):
            return jsonify(error='Enter a research goal of 1–4000 characters.'), 400
        target = browser_client()
        if target is None:
            return jsonify(error='The local browser service is unavailable.'), 503
        try:
            receipt = target.submit(task)
        except TaskQueueFull:
            return jsonify(error='The browser task queue is full.'), 429
        except (OSError, RuntimeError):
            return jsonify(error='The local browser service is unavailable.'), 503
        return jsonify(taskId=receipt.task_id, state=receipt.state, eventCursor=receipt.event_cursor), 202

    @app.get('/api/browser/tasks/<task_id>')
    def browser_task_status(task_id):
        raw_cursor = request.args.get('after', '0')
        if not raw_cursor.isdecimal() or len(raw_cursor) > 10:
            return jsonify(error='Event cursor must be a non-negative integer.'), 400
        target = browser_client()
        if target is None:
            return jsonify(error='The local browser service is unavailable.'), 503
        try:
            page = target.events(task_id, int(raw_cursor), DASHBOARD_SESSION_KEY)
        except (TaskNotFound, PermissionError):
            abort(404)
        except (OSError, RuntimeError):
            return jsonify(error='The local browser service is unavailable.'), 503
        return jsonify(
            taskId=page.task_id,
            state=page.state,
            events=[{
                'sequence': event.sequence,
                'timestamp': event.timestamp,
                'state': event.state,
                'summary': event.summary,
                'proposal': event.proposal,
            } for event in page.events],
            nextCursor=page.next_cursor,
            resetRequired=page.reset_required,
            result=page.result,
        )

    @app.post('/api/browser/tasks/<task_id>/control')
    def browser_task_control(task_id):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'command'} or payload['command'] not in {
            'pause', 'resume', 'stop',
        }:
            return jsonify(error='Choose pause, resume, or stop.'), 400
        target = browser_client()
        if target is None:
            return jsonify(error='The local browser service is unavailable.'), 503
        try:
            receipt = target.control(task_id, payload['command'], DASHBOARD_SESSION_KEY)
        except (TaskNotFound, PermissionError):
            abort(404)
        except (OSError, RuntimeError):
            return jsonify(error='The local browser service is unavailable.'), 503
        return jsonify(taskId=receipt.task_id, state=receipt.state, eventCursor=receipt.event_cursor)

    @app.post('/api/browser/tasks/<task_id>/decision')
    def browser_task_decision(task_id):
        payload = request.get_json(silent=True)
        if (not isinstance(payload, dict)
                or set(payload) != {'actionId', 'proposalDigest', 'approved'}
                or not isinstance(payload.get('actionId'), str)
                or not payload['actionId'] or len(payload['actionId']) > 128
                or not isinstance(payload.get('proposalDigest'), str)
                or len(payload['proposalDigest']) != 64
                or any(char not in '0123456789abcdef' for char in payload['proposalDigest'])
                or type(payload.get('approved')) is not bool):
            return jsonify(error='Choose approve or reject for the current action proposal.'), 400
        target = browser_client()
        if target is None:
            return jsonify(error='The local browser service is unavailable.'), 503
        try:
            receipt = target.decide(
                task_id, payload['actionId'], payload['proposalDigest'], payload['approved'],
                DASHBOARD_SESSION_KEY,
            )
        except (TaskNotFound, PermissionError):
            abort(404)
        except ValueError:
            return jsonify(error='That proposal is no longer current. Review the latest action.'), 409
        except (OSError, RuntimeError):
            return jsonify(error='The local browser service is unavailable.'), 503
        return jsonify(taskId=receipt.task_id, state=receipt.state, eventCursor=receipt.event_cursor)
