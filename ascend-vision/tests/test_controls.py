import sys
from types import SimpleNamespace
from unittest.mock import Mock

from config import SessionConfig
from controls import DesktopControls


def test_tray_menu_opens_pauses_and_quits(monkeypatch):
    icon = Mock()
    factory = Mock(return_value=icon)
    tray = SimpleNamespace(Icon=factory, Menu=lambda *items: items,
                           MenuItem=lambda text, callback, **kw: (text, callback, kw))
    opened = Mock()
    monkeypatch.setitem(sys.modules, 'pystray', tray)
    monkeypatch.setattr('controls.webbrowser.open', opened)
    manager = Mock(mode='focus')
    controls = DesktopControls(SessionConfig(), manager, url='http://127.0.0.1:1/')
    controls.start()
    items = factory.call_args.args[3]
    assert [text for text, _, _ in items] == ['Open Vision', 'Pause', 'Quit Vision']
    for _, callback, _ in items:
        callback(icon, None)
    opened.assert_called_once_with('http://127.0.0.1:1/')
    assert [c.args[0] for c in manager.request.call_args_list] == ['toggle', 'quit']
    manager.mode = 'background'
    controls.refresh()
    assert icon.title.endswith('(paused)')
    icon.update_menu.assert_called_once()
    controls.close()
    controls.close()
    icon.stop.assert_called_once()


def test_disabled_tray_does_not_import_pystray(monkeypatch):
    pystray = Mock()
    monkeypatch.setitem(sys.modules, 'pystray', pystray)
    controls = DesktopControls(SessionConfig(tray=False), Mock(mode='focus'))
    controls.start()
    controls.close()
    pystray.Icon.assert_not_called()


def test_tray_error_does_not_abort_logging(monkeypatch, caplog):
    pystray = Mock()
    pystray.Icon.side_effect = RuntimeError('no display')
    monkeypatch.setitem(sys.modules, 'pystray', pystray)
    controls = DesktopControls(SessionConfig(), Mock(mode='focus'))
    controls.start()
    controls.close()
    assert 'Tray unavailable' in caplog.text
