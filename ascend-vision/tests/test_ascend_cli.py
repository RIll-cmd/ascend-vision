from unittest.mock import patch

from config import AscendConfig, Config
from integrations.ascend_client import AscendConnectionState, AscendResult
import main


def test_ascend_diagnostic_reports_health_and_test_command(monkeypatch, caplog):
    caplog.set_level('INFO', logger='phone_watch')
    config = Config(ascend=AscendConfig())
    monkeypatch.setenv('ASCEND_BASE_URL', 'http://ascend.test:8000')
    monkeypatch.setenv('ASCEND_API_TOKEN', 'test-token')
    monkeypatch.setenv('ASCEND_CHARACTER_ID', 'char-test')

    class Client:
        def __init__(self, **kwargs):
            assert kwargs['base_url'] == 'http://ascend.test:8000'
            assert kwargs['api_token'] == 'test-token'

        def get_status(self):
            return AscendResult(AscendConnectionState.CONNECTED, {'status': 'ok'})

        def send_command(self, text, **kwargs):
            assert text == "What missions do I have today?"
            assert kwargs['source'] == 'phone'
            assert kwargs['character_id'] == 'char-test'
            return AscendResult(AscendConnectionState.CONNECTED,
                                {'success': True, 'message': 'Missions returned.'})

    with patch.object(main, 'load_config', return_value=config), \
         patch('integrations.ascend_client.AscendClient', Client):
        assert main.main(['--test-ascend']) == 0

    assert 'ASCEND_CONNECTED' in caplog.text
    assert 'Missions returned.' in caplog.text
