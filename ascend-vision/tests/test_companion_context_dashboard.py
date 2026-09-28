from config import Config, StorageConfig
from dashboard import create_app


class FakeContextClient:
    def __init__(self):
        self.paused = False
        self.intent = None
        self.duration = None
        self.snooze_seconds = None
        self.cleared = False
        self.fail = False

    def snapshot(self):
        if self.fail:
            raise OSError("private pipe detail")
        return {
            "schema_version": 1,
            "snapshot_id": "boot:2",
            "device_id": "local-laptop",
            "boot_id": "boot",
            "generated_at": "2026-09-27T08:00:00+00:00",
            "paused": self.paused,
            "snooze_until": "2026-09-27T08:30:00+00:00" if self.snooze_seconds else None,
            "fields": {
                "deskPresence": {"value": "present", "source": "webcam", "freshness": "fresh"},
                "desktopActivity": {"value": "input_active", "source": "desktop_activity", "freshness": "fresh"},
                "foregroundCategory": {"value": "development", "source": "desktop_activity", "freshness": "fresh"},
                "declaredIntent": {"value": self.intent or "none", "source": "user_declaration" if self.intent else "none", "freshness": "fresh" if self.intent else "unavailable"},
            },
        }

    def desk_region_status(self):
        return {
            "available": True,
            "calibrated": True,
            "calibrating": False,
            "region": {"left": 0.1, "top": 0.1, "right": 0.9, "bottom": 0.9},
        }

    def companion_decisions(self):
        if self.fail:
            raise OSError("pipe unavailable")
        return [{"evaluated_at": "2026-09-28T10:00:00+00:00", "mode": "shadow",
                 "rules": [{"rule_id": "break_suggestion", "trigger": "eligible",
                            "source": "focusSession", "evidence_age_seconds": 4,
                            "channel": "desktop_speech", "outcome": "shadowed",
                            "reason_code": "eligible"}]}]

    def begin_desk_calibration(self):
        return True

    def cancel_desk_calibration(self):
        return None

    def declare_intent(self, intent, *, duration_seconds=900):
        self.intent = intent
        self.duration = duration_seconds
        return "declaration-id"

    def clear(self):
        self.intent = None
        self.cleared = True

    def clear_intent(self):
        self.intent = None

    def set_snooze(self, *, duration_seconds=1800):
        self.snooze_seconds = duration_seconds

    def clear_snooze(self):
        self.snooze_seconds = None

    def set_paused(self, paused):
        self.paused = paused


def client_for(tmp_path, context):
    config = Config(storage=StorageConfig(database=tmp_path / "phone_watch.db"))
    return create_app(config, context_client=context).test_client()


def test_dashboard_reads_only_the_coarse_context_snapshot(tmp_path):
    context = FakeContextClient()
    client = client_for(tmp_path, context)

    response = client.get("/api/context")

    assert response.status_code == 200
    assert response.json["snapshot"]["fields"]["foregroundCategory"]["value"] == "development"
    assert "window_title" not in response.json["snapshot"]


def test_dashboard_exposes_sanitized_transient_companion_decisions(tmp_path):
    client = client_for(tmp_path, FakeContextClient())
    response = client.get("/api/context/companion-decisions")
    assert response.status_code == 200
    assert response.json["decisions"][0]["rules"][0]["outcome"] == "shadowed"
    assert "message" not in str(response.json)


def test_dashboard_accepts_bounded_temporary_intent_corrections(tmp_path):
    context = FakeContextClient()
    client = client_for(tmp_path, context)

    response = client.post("/api/context/intent", json={"intent": "break"})

    assert response.status_code == 200
    assert context.intent == "break"
    assert context.duration == 900
    assert client.get("/api/context").json["snapshot"]["fields"]["declaredIntent"]["value"] == "break"


def test_dashboard_rejects_unsupported_intent_or_unbounded_payload(tmp_path):
    client = client_for(tmp_path, FakeContextClient())

    unsupported = client.post("/api/context/intent", json={"intent": "productive"})
    oversized = client.post("/api/context/intent", json={"intent": "break", "duration_seconds": 100_000})

    assert unsupported.status_code == 400
    assert oversized.status_code == 400


def test_dashboard_clear_and_pause_controls_use_separate_commands(tmp_path):
    context = FakeContextClient()
    client = client_for(tmp_path, context)

    paused = client.post("/api/context/pause", json={"paused": True})
    cleared = client.post("/api/context/clear", json={})

    assert paused.status_code == 200
    assert context.paused is True
    assert cleared.status_code == 200
    assert context.cleared is True


def test_dashboard_can_undo_intent_and_set_a_bounded_snooze(tmp_path):
    context = FakeContextClient()
    client = client_for(tmp_path, context)
    client.post('/api/context/intent', json={'intent': 'research'})

    undo = client.post('/api/context/intent/clear', json={})
    snooze = client.post('/api/context/snooze', json={'duration_seconds': 1800})
    too_long = client.post('/api/context/snooze', json={'duration_seconds': 50_000})

    assert undo.status_code == 200
    assert context.intent is None
    assert snooze.status_code == 200
    assert context.snooze_seconds == 1800
    assert too_long.status_code == 400


def test_dashboard_hides_pipe_errors_and_reports_context_unavailable(tmp_path):
    context = FakeContextClient()
    context.fail = True
    client = client_for(tmp_path, context)

    response = client.get("/api/context")

    assert response.status_code == 503
    assert response.json == {"error": "Laptop context is unavailable."}


def test_dashboard_renders_accessible_context_inspection_and_control_labels(tmp_path):
    client = client_for(tmp_path, FakeContextClient())

    page = client.get("/").get_data(as_text=True)

    assert '<h2 id="context-heading">What Vision knows</h2>' in page
    assert 'id="context-intent-choice"' in page
    assert 'id="context-pause"' in page
    assert 'id="context-clear"' in page
    assert 'id="context-intent-undo"' in page
    assert 'id="context-snooze"' in page
    assert 'id="context-snooze-clear"' in page
    assert 'id="context-session"' in page
