import sys
from types import SimpleNamespace
from unittest.mock import Mock

from config import SessionConfig
from controls import DesktopControls


def test_tray_and_hotkey_queue_commands_and_release_resources(monkeypatch):
    icon = Mock()
    factory = Mock(return_value=icon)
    tray = SimpleNamespace(Icon=factory, Menu=lambda *items: items,
                           MenuItem=lambda text, callback, **kw: (text, callback, kw))
    keyboard = Mock()
    keyboard.add_hotkey.return_value = 'registration'
    monkeypatch.setitem(sys.modules, 'pystray', tray)
    monkeypatch.setitem(sys.modules, 'keyboard', keyboard)
    manager = Mock(mode='background')
    controls = DesktopControls(SessionConfig(), manager)
    controls.start()
    items = factory.call_args.args[3]
    for _, callback, _ in items:
        callback(icon, None)
    assert [c.args[0] for c in manager.request.call_args_list] == ['toggle', 'focus', 'background', 'quit']
    keyboard.add_hotkey.call_args.args[1]()
    manager.request.assert_called_with('toggle')
    assert keyboard.add_hotkey.call_args.kwargs == {'suppress': False, 'trigger_on_release': True}
    manager.mode = 'focus'
    controls.refresh()
    assert icon.title.endswith('focus')
    icon.update_menu.assert_called_once()
    controls.close()
    controls.close()
    keyboard.remove_hotkey.assert_called_once_with('registration')
    icon.stop.assert_called_once()


def test_disabled_controls_do_not_import_or_register_hooks(monkeypatch):
    keyboard = Mock()
    monkeypatch.setitem(sys.modules, 'keyboard', keyboard)
    controls = DesktopControls(SessionConfig(tray=False, hotkey_enabled=False), Mock(mode='background'))
    controls.start()
    controls.close()
    keyboard.add_hotkey.assert_not_called()


def test_hotkey_error_does_not_abort_logging(monkeypatch, caplog):
    keyboard = Mock()
    keyboard.add_hotkey.side_effect = ValueError('invalid chord')
    monkeypatch.setitem(sys.modules, 'keyboard', keyboard)
    controls = DesktopControls(SessionConfig(tray=False), Mock(mode='background'))
    controls.start()
    controls.close()
    assert 'Global hotkey unavailable' in caplog.text
