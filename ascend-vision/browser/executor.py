"""Playwright owner for one visible, disposable browser task."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import secrets
from pathlib import Path
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
                 max_pages: int = 3, profile_store=None, profile_owner: str | None = None,
                 profile_id: str | None = None, file_store=None, task_owner: str | None = None,
                 task_id: str | None = None, selected_file_token: str | None = None):
        if type(headless) is not bool or type(max_pages) is not int or not 1 <= max_pages <= 3:
            raise ValueError('invalid browser executor settings')
        self._policy = policy or BrowserPolicy()
        self._headless = headless
        self._max_pages = max_pages
        if (profile_id is None) != (profile_store is None or profile_owner is None):
            raise ValueError('saved browser profiles require a store, owner, and profile_id together')
        self._profile_store = profile_store
        self._profile_owner = profile_owner
        self._profile_id = profile_id
        if selected_file_token is not None and (file_store is None or not task_owner or not task_id):
            raise ValueError('selected uploads require owner, task, token, and file-store context')
        self._file_store = file_store
        self._task_owner = task_owner
        self._bound_task_id = task_id
        self._selected_file_token = selected_file_token
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
            context_options = {
                'service_workers': 'block',
                'accept_downloads': self._policy.allows_action('download', target_kind='page_link'),
            }
            if self._profile_id is not None:
                try:
                    context_options['storage_state'] = self._profile_store.load(
                        self._profile_owner, self._profile_id,
                    )
                except FileNotFoundError:
                    # A selected but not-yet-saved profile starts as a fresh isolated context.
                    pass
            self._context = self._browser.new_context(**context_options)
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
                    const type = tag === 'input' ? (node.type || 'text').toLowerCase() : '';
                    const label = node.getAttribute('aria-label') || node.getAttribute('placeholder') || node.labels?.[0]?.innerText || node.innerText || node.getAttribute('title') || '';
                    const normalizedLabel = String(label).replace(/\\s+/g, ' ').trim().slice(0, 240);
                    const options = tag === 'select' ? Array.from(node.options, option => [option.value, option.text]).slice(0, 100) : [];
                    const fingerprint = JSON.stringify({tag,type,role,name:node.getAttribute('name'),id:node.id,
                      aria:node.getAttribute('aria-label'),placeholder:node.getAttribute('placeholder'),
                      accept:node.getAttribute('accept'),multiple:node.hasAttribute('multiple'),required:node.hasAttribute('required'),
                      label:normalizedLabel,href:tag === 'a' ? node.href : '',text:String(node.innerText||'').slice(0,240),options});
                    return {tag, type, role, label:normalizedLabel, href:tag === 'a' ? node.href : '', fingerprint};
                }""")
                if info['tag'] == 'input' and info['type'] in {'password', 'hidden'}:
                    continue
                kind = 'page_link' if info['tag'] == 'a' and info['href'] else info['tag']
                if info['tag'] == 'input' and info['type'] == 'file':
                    kind = 'file_input'
                if info['tag'] == 'input' and info['type'] in {'submit', 'button', 'reset', 'image'}:
                    kind = 'button'
                ref = secrets.token_urlsafe(12)
                fingerprint = hashlib.sha256(str(info['fingerprint']).encode('utf-8')).hexdigest()
                self._element_handles[ref] = (kind, handle, str(info['href']), fingerprint)
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
        kind, handle, observed_href, fingerprint = target
        if not self._policy.allows_action('click', target_kind=kind):
            raise PermissionError('Public research can follow page links, but cannot activate this control.')
        try:
            connected = handle.evaluate('(node) => node.isConnected')
            href = handle.evaluate('(node) => node.href') if kind == 'page_link' else ''
        except Exception as exc:
            raise StaleObservation('This element is no longer attached to the page.') from exc
        if not connected:
            raise StaleObservation('This element is no longer attached to the page.')
        if href != observed_href:
            raise StaleObservation('This link changed after observation; inspect the page again before acting.')
        self._assert_element_unchanged(handle, fingerprint)
        if kind == 'page_link':
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

    def fill(self, task_id: str, observation_id: str, element_ref: str, value: str) -> BrowserObservation:
        self._require_page(task_id)
        if observation_id != self._latest_observation_id:
            raise StaleObservation('The page changed after this observation; observe again before acting.')
        target = self._element_handles.get(element_ref)
        if target is None:
            raise StaleObservation('This element reference is expired; observe the page again.')
        kind, handle, _href, fingerprint = target
        if kind not in {'input', 'textarea'} or not self._policy.allows_action('fill', target_kind=kind):
            raise PermissionError('This task cannot fill that page control.')
        if not isinstance(value, str) or not value.strip() or len(value) > 2_000:
            raise ValueError('The value must be nonempty and at most 2000 characters.')
        try:
            self._assert_element_unchanged(handle, fingerprint)
            details = handle.evaluate("node => ({connected:node.isConnected, tag:node.tagName.toLowerCase(), type:(node.type||'text').toLowerCase()})")
            if (not details['connected'] or details['tag'] not in {'input', 'textarea'}
                    or details['type'] in {'password', 'hidden', 'file', 'checkbox', 'radio', 'submit', 'button', 'reset', 'image'}):
                raise StaleObservation('This text control changed after observation.')
            handle.fill(value, timeout=5_000)
        except StaleObservation:
            raise
        except Exception as exc:
            raise StaleObservation('The text control is no longer available.') from exc
        self._document_revision += 1
        return self.observe(task_id)

    def select(self, task_id: str, observation_id: str, element_ref: str, value: str) -> BrowserObservation:
        self._require_page(task_id)
        if observation_id != self._latest_observation_id:
            raise StaleObservation('The page changed after this observation; observe again before acting.')
        target = self._element_handles.get(element_ref)
        if target is None:
            raise StaleObservation('This element reference is expired; observe the page again.')
        kind, handle, _href, fingerprint = target
        if kind != 'select' or not self._policy.allows_action('select', target_kind=kind):
            raise PermissionError('This task cannot change that selection.')
        if not isinstance(value, str) or not value.strip() or len(value) > 2_000:
            raise ValueError('The selected value must be nonempty and bounded.')
        try:
            self._assert_element_unchanged(handle, fingerprint)
            if not handle.evaluate('(node) => node.isConnected && node.tagName.toLowerCase() === "select"'):
                raise StaleObservation('This selection changed after observation.')
            handle.select_option(value=value, timeout=5_000)
        except StaleObservation:
            raise
        except Exception as exc:
            raise StaleObservation('The selection is no longer available.') from exc
        self._document_revision += 1
        return self.observe(task_id)

    def selected_upload(self) -> dict:
        if self._file_store is None or self._selected_file_token is None:
            raise PermissionError('This task has no explicitly selected upload file.')
        item = self._file_store.upload(self._task_owner, self._bound_task_id, self._selected_file_token)
        return {'filename': item.filename, 'sha256': item.sha256}

    def upload(self, task_id: str, observation_id: str, element_ref: str) -> BrowserObservation:
        self._require_page(task_id)
        if observation_id != self._latest_observation_id:
            raise StaleObservation('The page changed after this observation; observe again before acting.')
        target = self._element_handles.get(element_ref)
        if target is None:
            raise StaleObservation('This file input reference is expired; observe again.')
        kind, handle, _href, fingerprint = target
        if kind != 'file_input' or not self._policy.allows_action('upload', target_kind=kind):
            raise PermissionError('This task cannot upload to that page control.')
        item = self._file_store.upload(self._task_owner, task_id, self._selected_file_token)
        try:
            self._assert_element_unchanged(handle, fingerprint)
            details = handle.evaluate("node => ({connected:node.isConnected, type:(node.type||'').toLowerCase()})")
            if not details['connected'] or details['type'] != 'file':
                raise StaleObservation('The selected file input changed after observation.')
            handle.set_input_files({
                'name': item.filename,
                'mimeType': 'application/octet-stream',
                'buffer': item.path.read_bytes(),
            }, timeout=5_000)
        except StaleObservation:
            raise
        except Exception as exc:
            raise StaleObservation('The selected file input is no longer available.') from exc
        self._document_revision += 1
        return self.observe(task_id)

    def download(self, task_id: str, observation_id: str, element_ref: str) -> BrowserObservation:
        page = self._require_page(task_id)
        if observation_id != self._latest_observation_id:
            raise StaleObservation('The page changed after this observation; observe again before acting.')
        target = self._element_handles.get(element_ref)
        if target is None:
            raise StaleObservation('This download target is expired; observe again.')
        kind, handle, href, fingerprint = target
        if not self._policy.allows_action('download', target_kind=kind):
            raise PermissionError('Downloads are not enabled for this task.')
        try:
            if not handle.evaluate('(node) => node.isConnected'):
                raise StaleObservation('This download target is no longer attached to the page.')
            current_href = handle.evaluate('(node) => node.href') if kind == 'page_link' else ''
            if current_href != href:
                raise StaleObservation('This download destination changed after review; inspect it again.')
            self._assert_element_unchanged(handle, fingerprint)
            if kind == 'page_link':
                self._policy.validate_url(current_href)
        except StaleObservation:
            raise
        except Exception as exc:
            raise StaleObservation('This download target changed after review.') from exc
        if self._file_store is None or not self._task_owner:
            raise PermissionError('Managed browser downloads are not configured.')
        try:
            with page.expect_download(timeout=15_000) as pending:
                handle.click(timeout=5_000)
            download = pending.value
            if download.failure():
                raise RuntimeError('The browser download did not complete.')
            source = download.path()
            with open(source, 'rb') as stream:
                item = self._file_store.save_download(
                    self._task_owner, task_id, download.suggested_filename, stream,
                )
            self._last_download = {'filename': item.filename, 'size': item.size, 'sha256': item.sha256}
        except Exception:
            raise
        self._document_revision += 1
        return self.observe(task_id)

    @property
    def last_download(self):
        item = getattr(self, '_last_download', None)
        self._last_download = None
        return item

    @staticmethod
    def _assert_element_unchanged(handle, expected_fingerprint: str) -> None:
        try:
            fingerprint = handle.evaluate("""node => {
              const tag=node.tagName.toLowerCase();
              const type=tag==='input'?(node.type||'text').toLowerCase():'';
              const role=node.getAttribute('role')||({a:'link',button:'button',input:'textbox',textarea:'textbox',select:'combobox'}[tag]||tag);
              const label=node.getAttribute('aria-label')||node.getAttribute('placeholder')||node.labels?.[0]?.innerText||node.innerText||node.getAttribute('title')||'';
              const normalizedLabel=String(label).replace(/\\s+/g,' ').trim().slice(0,240);
              const options=tag==='select'?Array.from(node.options,option=>[option.value,option.text]).slice(0,100):[];
              return JSON.stringify({tag,type,role,name:node.getAttribute('name'),id:node.id,
                aria:node.getAttribute('aria-label'),placeholder:node.getAttribute('placeholder'),
                accept:node.getAttribute('accept'),multiple:node.hasAttribute('multiple'),required:node.hasAttribute('required'),
                label:normalizedLabel,href:tag==='a'?node.href:'',text:String(node.innerText||'').slice(0,240),options});
            }""")
        except Exception as exc:
            raise StaleObservation('This browser target is no longer available.') from exc
        actual = hashlib.sha256(str(fingerprint).encode('utf-8')).hexdigest()
        if actual != expected_fingerprint:
            raise StaleObservation('This browser target changed after review; inspect it and approve a fresh proposal.')

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

    def save_profile(self) -> None:
        """Persist the current login state only after an explicit owner request."""
        if self._context is None or self._profile_store is None or self._profile_id is None:
            raise PermissionError('This browser task has no owner-selected saved profile.')
        state = self._context.storage_state()
        self._profile_store.save(self._profile_owner, self._profile_id, state)

    def __exit__(self, *_exc_info) -> None:
        self.close()
