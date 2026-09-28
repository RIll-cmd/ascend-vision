from datetime import date, datetime, timezone

from assistant.activity_summary import ActivityHistoryStore
from config import Config, DashboardConfig, StorageConfig
from dashboard import create_app


def make_client(tmp_path, *, store=None):
    config = Config(storage=StorageConfig(database=tmp_path / "watch.db"),
                    dashboard=DashboardConfig(timezone="UTC"))
    app = create_app(config, activity_history_store=store)
    app.config["TESTING"] = True
    return app.test_client()


def test_history_is_off_by_default_and_requires_an_explicit_boolean_opt_in(tmp_path):
    store = ActivityHistoryStore(tmp_path / "activity.db")
    client = make_client(tmp_path, store=store)

    status = client.get("/api/activity-history?date=2026-09-27")
    assert status.status_code == 200
    assert status.json["enabled"] is False
    assert status.json["retention_days"] == 30
    assert status.json["score_enabled"] is False
    assert status.json["collection_available"] is False
    assert status.json["confirmed_outcomes_available"] is False
    assert "not in this local aggregate endpoint" in status.json["confirmed_outcomes_note"]
    assert "when authenticated access is available" in status.json["confirmed_outcomes_note"]

    assert client.put("/api/activity-history/settings", json={"enabled": True}).json["enabled"] is True
    assert client.put("/api/activity-history/settings", json={"enabled": 1}).status_code == 400
    assert client.get("/api/activity-history?date=2026-09-27").json["enabled"] is True


def test_summary_correction_export_and_delete_are_user_controlled(tmp_path):
    store = ActivityHistoryStore(tmp_path / "activity.db", now=lambda: datetime(2026, 9, 27, tzinfo=timezone.utc))
    store.set_enabled(True)
    store.add_interval(date(2026, 9, 27), "UTC", tracked_seconds=120,
                       focus_session_seconds=120, focus_coverage_seconds=120)
    client = make_client(tmp_path, store=store)

    summary = client.get("/api/activity-history?date=2026-09-27")
    assert summary.status_code == 200
    assert summary.json["days"][0]["focus_session_seconds"] == 120
    assert summary.json["confirmed_outcomes_available"] is False
    assert summary.json["score"] is None

    correction = client.post("/api/activity-history/corrections", json={
        "date": "2026-09-27", "metric": "focus_session_seconds", "remove_minutes": 1,
    })
    assert correction.status_code == 200
    assert client.get("/api/activity-history?date=2026-09-27").json["days"][0]["focus_session_seconds"] == 60
    exported = client.get("/api/activity-history/export")
    assert exported.status_code == 200
    assert exported.headers["Content-Disposition"].endswith('"vision-activity-history.json"')
    assert exported.json["summaries"][0]["corrections"][0]["removed_seconds"] == 60

    assert client.delete("/api/activity-history").json["deleted"] is True
    assert client.get("/api/activity-history?date=2026-09-27").json["days"] == []


def test_default_profile_history_survives_dashboard_recreation_and_user_lifecycle(tmp_path):
    profile = tmp_path / "disposable-profile"
    profile.mkdir()
    config = Config(
        storage=StorageConfig(database=profile / "watch.db"),
        dashboard=DashboardConfig(timezone="UTC"),
    )
    today = datetime.now(timezone.utc).date()

    first_app = create_app(config)
    first_app.config["TESTING"] = True
    first_client = first_app.test_client()
    assert first_client.get(f"/api/activity-history?date={today.isoformat()}").json["enabled"] is False
    assert first_client.put("/api/activity-history/settings", json={"enabled": True}).json["enabled"] is True

    # Simulate the collector writing to the default profile database, not an injected test store.
    collector_store = ActivityHistoryStore(profile / "activity_history.db")
    assert collector_store.add_interval(
        today, "UTC", tracked_seconds=120, focus_session_seconds=120, focus_coverage_seconds=120,
    ) is True

    restarted_app = create_app(config)
    restarted_app.config["TESTING"] = True
    restarted_client = restarted_app.test_client()
    summary = restarted_client.get(f"/api/activity-history?date={today.isoformat()}")
    assert summary.json["enabled"] is True
    assert summary.json["days"][0]["focus_session_seconds"] == 120

    correction = restarted_client.post("/api/activity-history/corrections", json={
        "date": today.isoformat(), "metric": "focus_session_seconds", "remove_minutes": 1,
    })
    assert correction.status_code == 200
    export = restarted_client.get("/api/activity-history/export")
    assert export.json["summaries"][0]["focus_session_seconds"] == 60

    assert restarted_client.delete("/api/activity-history").json["deleted"] is True
    final_app = create_app(config)
    final_app.config["TESTING"] = True
    assert final_app.test_client().get(f"/api/activity-history?date={today.isoformat()}").json["days"] == []


def test_activity_history_rejects_future_dates_and_invalid_corrections(tmp_path):
    store = ActivityHistoryStore(tmp_path / "activity.db")
    client = make_client(tmp_path, store=store)

    assert client.get("/api/activity-history?date=not-a-date").status_code == 400
    assert client.get("/api/activity-history?date=2099-01-01").status_code == 400
    assert client.post("/api/activity-history/corrections", json={
        "date": "2026-09-27", "metric": "mission_xp", "remove_minutes": 10,
    }).status_code == 400


def test_dashboard_explains_opt_in_scope_and_precise_metric_names(tmp_path):
    page = make_client(tmp_path).get("/").get_data(as_text=True)
    assert "Daily reflection" in page
    assert "Tracked focus-session minutes" in page
    assert "Observed entertainment-category minutes" in page
    assert "scoring is disabled" in page
    assert "This local activity panel does not include Core-confirmed mission outcomes" in page
