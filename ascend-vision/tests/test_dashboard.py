from dataclasses import replace
import sqlite3

import pytest

from config import Config, StorageConfig, DashboardConfig
from dashboard import create_app, main


@pytest.fixture
def client(tmp_path):
    cfg = Config(storage=StorageConfig(database=tmp_path/'watch.db'))
    app = create_app(cfg)
    app.config['TESTING'] = True
    return app.test_client()


def test_dashboard_starts_without_database_and_serves_only_local_assets(client):
    response = client.get('/')
    assert response.status_code == 200
    assert b'/static/dashboard.js' in response.data
    assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
    assert client.get('/api/stats').json['database_exists'] is False
    assert client.get('/static/dashboard.js').status_code == 200
    assert client.get('/static/dashboard.css').status_code == 200
    assert client.get('/.env').status_code == 404
    assert client.post('/api/stats').status_code == 405


@pytest.mark.parametrize('query', ['mode=other', 'start=bad', 'start=2026-09-02&end=2026-09-01',
    'start=2020-01-01&end=2026-01-01', 'mode=focus&mode=background', 'file=secret', 'end=9999-12-31'])
def test_invalid_filters(client, query):
    assert client.get('/api/stats?'+query).status_code == 400


def test_rebinding_and_cross_site_reads_rejected(client):
    assert client.get('/api/stats', headers={'Host': 'evil.example'}).status_code == 400
    assert client.get('/api/stats', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.get('/api/stats', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert 'Access-Control-Allow-Origin' not in client.get('/api/stats').headers


def test_database_failure_is_not_a_zero_total(tmp_path):
    path = tmp_path/'bad.db'
    path.write_bytes(b'not sqlite')
    app = create_app(Config(storage=StorageConfig(database=path)))
    response = app.test_client().get('/api/stats')
    assert response.status_code == 503
    assert 'error' in response.json and 'summary' not in response.json
    assert str(tmp_path) not in response.get_data(as_text=True)


@pytest.mark.parametrize('values', [dict(port=80), dict(port=True), dict(default_days=0),
    dict(refresh_seconds=0), dict(timezone='invalid/zone'), dict(open_browser='yes')])
def test_dashboard_config_validation(values):
    with pytest.raises(ValueError):
        DashboardConfig(**values)


def test_cli_invalid_config_does_not_start_server(tmp_path):
    assert main(['--config', str(tmp_path/'missing.yaml'), '--no-open-browser']) == 1
