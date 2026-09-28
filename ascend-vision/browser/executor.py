"""Playwright owner for one visible, disposable browser task."""

from dataclasses import dataclass
from datetime import datetime, timezone
import secrets
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from browser.policy import BrowserPolicy, PolicyDenied


MAX_VISIBLE_TEXT_CHARACTERS = 12_000
MAX_INTERACTIVE_ELEMENTS = 100


class BrowserUnavailable(RuntimeError):
    """The optional Playwright runtime is not installed or could not launch."""


class StaleObservation(RuntimeError):
    """An action referenced page evidence that is no longer current."""


@dataclass(frozen=True)
class BrowserElement:
    element_ref: str
    role: str
    label: str
    kind: str


@dataclass(frozen=True)
class BrowserObservation:
    task_id: str
    page_id: str
    document_revision: int
    observation_id: str
    observed_at: str
    url: str
    title: str
    visible_text: str
    elements: tuple[BrowserElement, ...]
    truncated: bool


class BrowserExecutor:
    """Runs Playwright synchronously; all methods must stay on one owner thread."""

    def __init__(self, *, policy: BrowserPolicy | None = None, headless: bool = False,
                 max_pages: int = 3):
        if type(headless) is not bool or type(max_pages) is not int or not 1 <= max_pages <= 3:
            raise ValueError('invalid browser executor settings')
        self._policy = policy or BrowserPolicy()
        self._headless = headless
        self._max_pages = max_pages
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._pages: list[Any] = []
        self._task_id: str | None = None
        self._page_id = secrets.token_urlsafe(12)
        self._document_revision = 0
        self._latest_observation_id: str | None = None
        self._element_handles: dict[str, tuple[str, Any, str]] = {}
        self._request_blocked = False

    def __enter__(self) -> 'BrowserExecutor':
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserUnavailable(
                'Browser automation needs the optional requirements-browser.txt installation.'
            ) from exc
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self._headless)
            self._context = self._browser.new_context(
                service_workers='block', accept_downloads=False,
            )
            self._context.set_default_timeout(5_000)
            self._context.set_default_navigation_timeout(15_000)
            self._context.route('**/*', self._guard_request)
            self._context.route_web_socket('**/*', self._block_web_socket)
            self._context.on('page', self._on_page)
            self._page = self._context.new_page()
            self._pages = [self._page]
            return self
        except Exception as exc:
            self.close()
            if isinstance(exc, BrowserUnavailable):
                raise
            raise BrowserUnavailable('The isolated Chromium browser could not be started.') from exc

    def _on_page(self, page) -> None:
        self._pages.append(page)
        if len(self._pages) > self._max_pages:
            page.close()

    def _guard_request(self, route) -> None:
        try:
            self._policy.validate_url(route.request.url)
            # `continue_()` only routes the original URL; Playwright follows a
            # redirect without invoking this handler for the destination. Fetch
            # one hop ourselves and refuse redirects so every destination is
            # validated before the browser can contact it.
            response = route.fetch(max_redirects=0, timeout=15_000)
            if 300 <= response.status < 400:
                self._request_blocked = True
                route.abort('blockedbyclient')
                return
            route.fulfill(response=response)
        except (PolicyDenied, ValueError):
            self._request_blocked = True
            route.abort('blockedbyclient')
        except Exception:
            # Fail closed if the request cannot be safely inspected/fulfilled.
            self._request_blocked = True
            try:
                route.abort('blockedbyclient')
            except Exception:
                pass

    def _block_web_socket(self, route) -> None:
        # BrowserContext.route() does not cover WebSocket handshakes. Public
        # research has no supported need for sockets. Playwright does not
        # connect routed WebSockets unless the handler explicitly calls
        # connect_to_server(); leaving it unconnected blocks the destination.
        self._request_blocked = True

    def _require_page(self, task_id: str):
        if not isinstance(task_id, str) or not task_id:
            raise ValueError('task_id is required')
        if self._context is None:
            raise BrowserUnavailable('The browser executor is not running.')
        if self._task_id is None:
            self._task_id = task_id
        elif self._task_id != task_id:
            raise PermissionError('This browser context belongs to another task.')
        if self._page is None or self._page.is_closed():
            raise BrowserUnavailable('The task browser page is closed.')
        return self._page

    def navigate(self, task_id: str, url: str) -> BrowserObservation:
        self._require_page(task_id)
        self._policy.validate_url(url)
        self._request_blocked = False
        try:
            self._page.goto(url, wait_until='domcontentloaded', timeout=15_000)
        except Exception as exc:
            if self._request_blocked or 'blockedbyclient' in str(exc).lower().replace('_', ''):
                raise PolicyDenied('A request was blocked by the public-research network policy.') from exc
            raise
        if self._request_blocked:
            raise PolicyDenied('A request was blocked by the public-research network policy.')
        self._document_revision += 1
        return self.observe(task_id)

    def observe(self, task_id: str) -> BrowserObservation:
        page = self._require_page(task_id)
        self._latest_observation_id = secrets.token_urlsafe(18)
        self._element_handles.clear()
        try:
            visible_text = page.locator('body').inner_text(timeout=5_000)
            title = page.title()
            current_url = page.url
        except Exception as exc:
            raise BrowserUnavailable('The current page could not be observed.') from exc
        truncated = len(visible_text) > MAX_VISIBLE_TEXT_CHARACTERS
        visible_text = visible_text[:MAX_VISIBLE_TEXT_CHARACTERS]
        elements = []
        locators = page.locator(
            'a,button,input,textarea,select,[role="link"],[role="button"],'
            '[role="textbox"],[role="combobox"]'
        ).all()
        for locator in locators[:MAX_INTERACTIVE_ELEMENTS]:
            try:
                if not locator.is_visible():
                    continue
                handle = locator.element_handle(timeout=500)
                if handle is None:
                    continue
                info = handle.evaluate("""node => {
                    const tag = node.tagName.toLowerCase();
                    const role = node.getAttribute('role') || ({a:'link',button:'button',input:'textbox',textarea:'textbox',select:'combobox'}[tag] || tag);
                    const label = node.getAttribute('aria-label') || node.getAttribute('placeholder') || node.innerText || node.getAttribute('title') || '';
                    return {tag, role, label: String(label).replace(/\\s+/g, ' ').trim().slice(0, 240), href: tag === 'a' ? node.href : ''};
                }""")
                kind = 'page_link' if info['tag'] == 'a' and info['href'] else info['tag']
                ref = secrets.token_urlsafe(12)
                self._element_handles[ref] = (kind, handle, str(info['href']))
                elements.append(BrowserElement(ref, str(info['role'])[:64], str(info['label']), kind))
            except Exception:
                # A concurrently changing page is observed again; stale nodes are never guessed.
                continue
        return BrowserObservation(
            task_id=task_id,
            page_id=self._page_id,
            document_revision=self._document_revision,
            observation_id=self._latest_observation_id,
            observed_at=datetime.now(timezone.utc).isoformat(),
            url=self._sanitize_url(current_url),
            title=str(title)[:300],
            visible_text=visible_text,
            elements=tuple(elements),
            truncated=truncated or len(locators) > MAX_INTERACTIVE_ELEMENTS,
        )

    @staticmethod
    def _sanitize_url(url: str) -> str:
        parsed = urlsplit(url)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ''))

    def click(self, task_id: str, observation_id: str, element_ref: str) -> BrowserObservation:
        page = self._require_page(task_id)
        if observation_id != self._latest_observation_id:
            raise StaleObservation('The page changed after this observation; observe again before acting.')
        target = self._element_handles.get(element_ref)
        if target is None:
            raise StaleObservation('This element reference is expired; observe the page again.')
        kind, handle, observed_href = target
        if not self._policy.allows_action('click', target_kind=kind):
            raise PermissionError('Public research can follow page links, but cannot activate this control.')
        try:
            connected = handle.evaluate('(node) => node.isConnected')
            href = handle.evaluate('(node) => node.href')
        except Exception as exc:
            raise StaleObservation('This element is no longer attached to the page.') from exc
        if not connected:
            raise StaleObservation('This element is no longer attached to the page.')
        if href != observed_href:
            raise StaleObservation('This link changed after observation; inspect the page again before acting.')
        self._policy.validate_url(href)
        before = set(id(item) for item in self._pages if not item.is_closed())
        handle.click(timeout=5_000)
        self._page.wait_for_timeout(100)
        current_pages = [item for item in self._pages if not item.is_closed()]
        if len(current_pages) > len(before):
            self._page = current_pages[-1]
            self._page_id = secrets.token_urlsafe(12)
        self._document_revision += 1
        return self.observe(task_id)

    def scroll(self, task_id: str, observation_id: str, direction: str, pixels: int) -> BrowserObservation:
        page = self._require_page(task_id)
        if observation_id != self._latest_observation_id:
            raise StaleObservation('The page changed after this observation; observe again before acting.')
        if direction not in {'up', 'down'} or type(pixels) is not int or not 1 <= pixels <= 2_000:
            raise ValueError('scroll direction or distance is invalid')
        page.mouse.wheel(0, -pixels if direction == 'up' else pixels)
        self._document_revision += 1
        return self.observe(task_id)

    def back(self, task_id: str, observation_id: str) -> BrowserObservation:
        page = self._require_page(task_id)
        if observation_id != self._latest_observation_id:
            raise StaleObservation('The page changed after this observation; observe again before acting.')
        page.go_back(wait_until='domcontentloaded', timeout=10_000)
        self._document_revision += 1
        return self.observe(task_id)

    def wait_for(self, task_id: str, observation_id: str, milliseconds: int) -> BrowserObservation:
        page = self._require_page(task_id)
        if observation_id != self._latest_observation_id:
            raise StaleObservation('The page changed after this observation; observe again before acting.')
        if type(milliseconds) is not int or not 1 <= milliseconds <= 10_000:
            raise ValueError('wait_for must be between 1 and 10000 milliseconds')
        page.wait_for_timeout(milliseconds)
        return self.observe(task_id)

    def close(self) -> None:
        context, browser, playwright = self._context, self._browser, self._playwright
        self._context = self._browser = self._playwright = self._page = None
        self._element_handles.clear()
        self._pages.clear()
        for resource in (context, browser, playwright):
            if resource is not None:
                try:
                    resource.close() if resource is not playwright else resource.stop()
                except Exception:
                    pass

    def __exit__(self, *_exc_info) -> None:
        self.close()
