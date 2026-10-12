"""Optional system-tray control. Callbacks enqueue requests; they never touch SQLite."""
import logging
import webbrowser

LOG = logging.getLogger(__name__)


class DesktopControls:
    def __init__(self, config, manager, url=None):
        self.config = config
        self.manager = manager
        self.url = url
        self._icon = None
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

    def _open(self):
        if self.url:
            webbrowser.open(self.url)

    def start(self):
        if not self.config.tray:
            return
        try:
            import pystray
            menu = pystray.Menu(
                pystray.MenuItem('Open Vision', lambda icon, item: self._open(), default=True),
                pystray.MenuItem('Pause', lambda icon, item: self.manager.request('toggle'),
                                 checked=lambda item: self.manager.mode != 'focus'),
                pystray.MenuItem('Quit Vision', lambda icon, item: self.manager.request('quit')))
            self._icon = pystray.Icon('ascend_vision', self.image(self.manager.mode),
                                     self._title(), menu)
            self._icon.run_detached()
            LOG.info('Tray controls started')
        except Exception as exc:
            LOG.warning('Tray unavailable: %s. Use the Vision window.', exc)
            self._stop_icon()

    def _title(self):
        return 'Vision' if self.manager.mode == 'focus' else 'Vision (paused)'

    def refresh(self):
        if self.manager.mode == self._last_mode:
            return
        self._last_mode = self.manager.mode
        if self._icon is not None:
            try:
                self._icon.title = self._title()
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
        self._stop_icon()
