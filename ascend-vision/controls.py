"""Optional desktop controls. Callbacks enqueue requests; they never touch SQLite."""
import logging

LOG = logging.getLogger(__name__)


class DesktopControls:
    def __init__(self, config, manager):
        self.config = config
        self.manager = manager
        self._icon = None
        self._keyboard = None
        self._hotkey = None
        self._last_mode = manager.mode

    @staticmethod
    def image(mode):
        from PIL import Image, ImageDraw
        image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((7, 3, 57, 61), radius=10,
                               fill='#d97324' if mode == 'focus' else '#21826b')
        draw.rounded_rectangle((20, 10, 44, 53), radius=4, outline='white', width=3)
        draw.line((28, 47, 36, 47), fill='white', width=2)
        return image

    def start(self):
        if self.config.tray:
            try:
                import pystray
                menu = pystray.Menu(
                    pystray.MenuItem('Focus mode', lambda icon, item: self.manager.request('toggle'),
                                     checked=lambda item: self.manager.mode == 'focus', default=True),
                    pystray.MenuItem('Start focus', lambda icon, item: self.manager.request('focus')),
                    pystray.MenuItem('Return to background', lambda icon, item: self.manager.request('background')),
                    pystray.MenuItem('Quit Phone Watch', lambda icon, item: self.manager.request('quit')))
                self._icon = pystray.Icon('phone_watch', self.image(self.manager.mode),
                                         f'Phone Watch — {self.manager.mode}', menu)
                self._icon.run_detached()
                LOG.info('Tray controls started')
            except Exception as exc:
                LOG.warning('Tray unavailable: %s. Use preview Space or --focus.', exc)
                self._stop_icon()
        if self.config.hotkey_enabled:
            try:
                import keyboard
                self._keyboard = keyboard
                self._hotkey = keyboard.add_hotkey(self.config.hotkey,
                    lambda: self.manager.request('toggle'), suppress=False, trigger_on_release=True)
                LOG.info('Focus hotkey registered: %s', self.config.hotkey)
            except Exception as exc:
                LOG.warning('Global hotkey unavailable: %s. Use tray or preview Space.', exc)

    def refresh(self):
        if self.manager.mode == self._last_mode:
            return
        self._last_mode = self.manager.mode
        if self._icon is not None:
            try:
                self._icon.title = f'Phone Watch — {self.manager.mode}'
                self._icon.icon = self.image(self.manager.mode)
                self._icon.update_menu()
            except Exception as exc:
                LOG.warning('Tray refresh failed: %s', exc)

    def _stop_icon(self):
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception as exc:
                LOG.warning('Tray shutdown failed: %s', exc)
            finally:
                self._icon = None

    def close(self):
        try:
            if self._hotkey is not None:
                try:
                    self._keyboard.remove_hotkey(self._hotkey)
                except Exception as exc:
                    LOG.warning('Hotkey cleanup failed: %s', exc)
                finally:
                    self._hotkey = None
        finally:
            self._stop_icon()
