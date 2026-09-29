"""Authenticated-local-dashboard browser task submission and control routes."""

import time
import uuid
import json

from flask import abort, jsonify, request

from browser.contracts import BrowserTaskRequest
from browser.files import FileLimitExceeded
from browser.service import TaskNotFound, TaskQueueFull


DASHBOARD_SESSION_KEY = ('local', 'dashboard', 'local-dashboard')


def register_browser_routes(app, client, config, file_store=None) -> None:
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
        upload = None
        if request.mimetype == 'multipart/form-data':
            payload = request.form.to_dict()
            upload = request.files.get('upload')
            if 'capabilities' in payload:
                try:
                    payload['capabilities'] = json.loads(payload['capabilities'])
                except (TypeError, json.JSONDecodeError):
                    return jsonify(error='Browser actions are malformed.'), 400
        else:
            payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or 'goal' not in payload:
            return jsonify(error='Provide one browser task goal.'), 400
        scope_mode = 'public_research'
        request_fields = {
            'schema_version': 1,
            'task_id': request.headers.get('Idempotency-Key') or uuid.uuid4().hex,
            'session_key': list(DASHBOARD_SESSION_KEY),
            'goal': payload.get('goal'),
            'provider': config.provider,
            'scope_mode': scope_mode,
            'expires_at': time.time() + config.task_timeout_seconds,
        }
        if config.b3_enabled and payload.get('scopeMode') == 'selected_origins':
            if set(payload) != {'goal', 'scopeMode', 'origin', 'capabilities', 'profileId'}:
                return jsonify(error='Provide the selected origin, allowed actions, and optional profile name.'), 400
            capabilities = payload.get('capabilities')
            if (not isinstance(capabilities, list) or len(capabilities) > 5
                    or any(not isinstance(item, str) or item not in {'fill', 'select', 'click', 'upload', 'download'} for item in capabilities)
                    or len(set(capabilities)) != len(capabilities)):
                return jsonify(error='Choose only supported, unique browser actions.'), 400
            profile_id = payload.get('profileId')
            if profile_id == '':
                profile_id = None
            scope_mode = 'selected_origins'
            request_fields.update({
                'scope_mode': scope_mode,
                'allowed_origins': [payload.get('origin')],
                'capabilities': capabilities,
            })
            if profile_id is not None:
                request_fields['profile_id'] = profile_id
            if upload is not None:
                if (file_store is None or 'upload' not in capabilities
                        or not isinstance(upload.filename, str) or not upload.filename.strip()):
                    return jsonify(error='Select one file and enable the upload action for this task.'), 400
                try:
                    token, _item = file_store.stage_upload(
                        DASHBOARD_SESSION_KEY[0], request_fields['task_id'], upload.filename, upload.stream,
                    )
                except FileLimitExceeded as exc:
                    return jsonify(error=str(exc)), 413
                except (OSError, ValueError, FileExistsError):
                    return jsonify(error='The selected file could not be staged safely.'), 400
                request_fields['selected_file_token'] = token
        elif set(payload) != {'goal'}:
            return jsonify(error='Authenticated browser options are available only when B3 is enabled.'), 400
        try:
            task = BrowserTaskRequest.from_payload(request_fields)
        except (TypeError, ValueError):
            if upload is not None and file_store is not None and 'selected_file_token' in request_fields:
                file_store.cleanup_task(DASHBOARD_SESSION_KEY[0], request_fields['task_id'])
            return jsonify(error='Enter a research goal of 1–4000 characters.'), 400
        try:
            target = browser_client()
        except (OSError, RuntimeError):
            if upload is not None and file_store is not None:
                file_store.cleanup_task(DASHBOARD_SESSION_KEY[0], request_fields['task_id'])
            return jsonify(error='The local browser service is unavailable.'), 503
        if target is None:
            if upload is not None and file_store is not None:
                file_store.cleanup_task(DASHBOARD_SESSION_KEY[0], request_fields['task_id'])
            return jsonify(error='The local browser service is unavailable.'), 503
        try:
            receipt = target.submit(task)
        except TaskQueueFull:
            if upload is not None and file_store is not None:
                file_store.cleanup_task(DASHBOARD_SESSION_KEY[0], request_fields['task_id'])
            return jsonify(error='The browser task queue is full.'), 429
        except ValueError:
            if upload is not None and file_store is not None:
                file_store.cleanup_task(DASHBOARD_SESSION_KEY[0], request_fields['task_id'])
            return jsonify(error='That idempotency key is already bound to a different browser request.'), 409
        except (OSError, RuntimeError):
            if upload is not None and file_store is not None:
                file_store.cleanup_task(DASHBOARD_SESSION_KEY[0], request_fields['task_id'])
            return jsonify(error='The local browser service is unavailable.'), 503
        return jsonify(taskId=receipt.task_id, state=receipt.state, eventCursor=receipt.event_cursor), 202

    @app.get('/api/browser/routines')
    def browser_routine_catalogue():
        target = browser_client()
        if target is None:
            return jsonify(error='The local browser service is unavailable.'), 503
        try:
            return jsonify(routines=list(target.routine_catalogue()))
        except (OSError, RuntimeError):
            return jsonify(error='The local browser service is unavailable.'), 503

    @app.post('/api/browser/routine-tasks')
    def submit_browser_routine():
        from browser.routines.contracts import RoutineInvocation
        payload = request.get_json(silent=True)
        try:
            invocation = RoutineInvocation.from_payload(payload)
        except (TypeError, ValueError):
            return jsonify(error='Choose an installed routine and provide only its supported inputs.'), 400
        task_id = request.headers.get('Idempotency-Key') or uuid.uuid4().hex
        try:
            task = BrowserTaskRequest.from_payload({
                'schema_version': 1, 'task_id': task_id,
                'session_key': list(DASHBOARD_SESSION_KEY),
                'goal': 'Run the selected supported browser routine.',
                'provider': config.provider, 'scope_mode': 'public_research',
                'expires_at': time.time() + config.task_timeout_seconds,
            })
        except (TypeError, ValueError):
            return jsonify(error='The browser routine request is invalid.'), 400
        target = browser_client()
        if target is None:
            return jsonify(error='The local browser service is unavailable.'), 503
        try:
            receipt = target.submit_routine(task, invocation)
        except TaskQueueFull:
            return jsonify(error='The browser task queue is full.'), 429
        except PermissionError:
            return jsonify(error='This exact routine version is not enabled on this laptop.'), 409
        except ValueError:
            return jsonify(error='The routine version, supported inputs, or idempotency key did not match.'), 409
        except (OSError, RuntimeError):
            return jsonify(error='The local browser service is unavailable.'), 503
        return jsonify(taskId=receipt.task_id, state=receipt.state, eventCursor=receipt.event_cursor), 202

    @app.post('/api/browser/routines/<routine_id>/<int:version>/disable')
    def disable_browser_routine(routine_id, version):
        target = browser_client()
        if target is None:
            return jsonify(error='The local browser service is unavailable.'), 503
        try:
            target.disable_routine(routine_id, version)
        except ValueError:
            return jsonify(error='The routine reference is invalid.'), 400
        except (OSError, RuntimeError):
            return jsonify(error='The local browser service is unavailable.'), 503
        return jsonify(disabled=True)

    @app.post('/api/browser/routines/<routine_id>/<int:version>/enable')
    def enable_browser_routine(routine_id, version):
        target = browser_client()
        if target is None:
            return jsonify(error='The local browser service is unavailable.'), 503
        try:
            target.enable_routine(routine_id, version)
        except PermissionError:
            return jsonify(error='Only an owner-configured and accepted routine version can be enabled.'), 409
        except ValueError:
            return jsonify(error='The routine reference is invalid.'), 400
        except (OSError, RuntimeError):
            return jsonify(error='The local browser service is unavailable.'), 503
        return jsonify(enabled=True)

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

    @app.post('/api/browser/tasks/<task_id>/feedback')
    def browser_task_feedback(task_id):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'verdict'} or payload.get('verdict') not in {
            'worked', 'needed_correction', 'did_not_work',
        }:
            return jsonify(error='Choose Worked, Needed correction, or Did not work.'), 400
        target = browser_client()
        if target is None:
            return jsonify(error='The local browser service is unavailable.'), 503
        try:
            recorded = target.feedback(task_id, payload['verdict'], DASHBOARD_SESSION_KEY)
        except (TaskNotFound, PermissionError):
            abort(404)
        except ValueError:
            return jsonify(error='Feedback is available after the task result is shown.'), 409
        except (OSError, RuntimeError):
            return jsonify(error='The local browser service is unavailable.'), 503
        if not recorded:
            return jsonify(error='Metrics collection is off or this task is no longer in the current session.'), 409
        return jsonify(recorded=True)

    @app.get('/api/browser/metrics')
    def browser_metrics_summary():
        cohort = request.args.get('cohort', 'all')
        if cohort not in {'all', 'live_public', 'pwa', 'discord', 'signed_in', 'fixture'}:
            return jsonify(error='Choose a supported metrics cohort.'), 400
        target = browser_client()
        if target is None:
            return jsonify(error='The local browser service is unavailable.'), 503
        try:
            return jsonify(target.metrics_summary(cohort))
        except (OSError, RuntimeError):
            return jsonify(error='The local browser service is unavailable.'), 503

    @app.post('/api/browser/metrics/settings')
    def browser_metrics_settings():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'enabled'} or type(payload.get('enabled')) is not bool:
            return jsonify(error='Metrics setting must be true or false.'), 400
        target = browser_client()
        if target is None:
            return jsonify(error='The local browser service is unavailable.'), 503
        try:
            return jsonify(target.metrics_set_enabled(payload['enabled']))
        except (OSError, RuntimeError):
            return jsonify(error='The local browser service is unavailable.'), 503

    @app.get('/api/browser/metrics/export')
    def browser_metrics_export():
        raw_cursor = request.args.get('cursor', '0')
        if not raw_cursor.isdecimal() or len(raw_cursor) > 8:
            return jsonify(error='Export cursor must be a non-negative integer.'), 400
        target = browser_client()
        if target is None:
            return jsonify(error='The local browser service is unavailable.'), 503
        try:
            return jsonify(target.metrics_export(int(raw_cursor)))
        except ValueError:
            return jsonify(error='Local metrics storage is unavailable.'), 409
        except (OSError, RuntimeError):
            return jsonify(error='The local browser service is unavailable.'), 503

    @app.delete('/api/browser/metrics')
    def browser_metrics_clear():
        if request.get_data(cache=False):
            return jsonify(error='Clear metrics does not accept a request body.'), 400
        target = browser_client()
        if target is None:
            return jsonify(error='The local browser service is unavailable.'), 503
        try:
            return jsonify(target.metrics_clear())
        except (OSError, RuntimeError):
            return jsonify(error='The local browser service is unavailable.'), 503

    @app.post('/api/browser/tasks/<task_id>/profile/save')
    def browser_task_profile_save(task_id):
        if not config.b3_enabled:
            abort(404)
        if request.get_data(cache=False):
            return jsonify(error='Profile save does not accept a request body.'), 400
        target = browser_client()
        if target is None:
            return jsonify(error='The local browser service is unavailable.'), 503
        try:
            receipt = target.save_profile(task_id, DASHBOARD_SESSION_KEY)
        except (TaskNotFound, PermissionError):
            abort(404)
        except ValueError:
            return jsonify(error='A profile can be saved only during an active human takeover.'), 409
        except (OSError, RuntimeError):
            return jsonify(error='The local browser service is unavailable.'), 503
        return jsonify(taskId=receipt.task_id, state=receipt.state, eventCursor=receipt.event_cursor), 202

    @app.get('/api/browser/profiles')
    def browser_profiles_list():
        if not config.b3_enabled:
            abort(404)
        target = browser_client()
        if target is None:
            return jsonify(error='The local browser service is unavailable.'), 503
        try:
            return jsonify(profiles=target.list_profiles(DASHBOARD_SESSION_KEY))
        except (OSError, RuntimeError):
            return jsonify(error='The local browser service is unavailable.'), 503

    @app.delete('/api/browser/profiles/<profile_id>')
    def browser_profile_clear(profile_id):
        if not config.b3_enabled:
            abort(404)
        target = browser_client()
        if target is None:
            return jsonify(error='The local browser service is unavailable.'), 503
        try:
            cleared = target.clear_profile(profile_id, DASHBOARD_SESSION_KEY)
        except ValueError:
            return jsonify(error='This profile is invalid or currently in use.'), 409
        except (OSError, RuntimeError):
            return jsonify(error='The local browser service is unavailable.'), 503
        return jsonify(cleared=cleared)
