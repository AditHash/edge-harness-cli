"""Browser control for the model, through Playwright's own Chromium.

A page is untrusted and can try to steer the model, so the browser never holds anything worth
stealing: a fresh profile each session with no logins, cookies or saved data, no downloads and no
page scripts run on the model's behalf. Password and payment fields are refused, opening a site needs
approval, and so does each click and typed text.

Every request a page makes is checked too, not only the address the model asked for, so a public page
cannot reach local services or the LAN. Local addresses work only on ports the user listed. A page
can still re-point its own domain at another address after the check, so this limits such attacks
rather than ruling them out.

Playwright objects belong to the thread that made them and tools run on changing threads, so all
browser work happens on one worker thread.
"""

import atexit
import contextlib
import importlib.util
import ipaddress
import queue
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from strands import tool
from strands.types.tools import ToolContext

from code_harness.guardrails import redact_secrets
from code_harness.mcp_remote import _is_public, _resolve
from code_harness.web import UNTRUSTED, WebError, validate_url

NAVIGATION_TIMEOUT_MS = 30_000
ACTION_TIMEOUT_MS = 10_000
MAX_TEXT_CHARS = 20_000
MAX_ELEMENTS = 80
MAX_CONSOLE_LINES = 50
MAX_TYPED_CHARS = 2000
INSTALL_HINT = "Browser not installed. Run: python -m playwright install chromium"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_FREE_SCHEMES = {"about", "data", "blob"}
_ORDERED_TOOLS = {"browser_open", "browser_click", "browser_type", "browser_press"}
_ALLOWED_KEYS = {"Enter", "Tab", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"}
_SENSITIVE_AUTOCOMPLETE = re.compile(
    r"^(?:.*\s)?(?:current-password|new-password|one-time-code|cc-\S+)$"
)
_SENSITIVE_NAME = re.compile(
    r"pass(?:word|wd|code|phrase)|secret|\bpin\b|\bcvv\b|\bcvc\b|card.?number|\bssn\b|\botp\b",
    re.IGNORECASE,
)
_LIST_ELEMENTS = """(limit) => {
  const selector = 'a[href],button,input,select,textarea,summary,[role=button],[role=link],[onclick]';
  const visible = (e) => {
    const box = e.getBoundingClientRect();
    const style = getComputedStyle(e);
    return box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  document.querySelectorAll('[data-eh-ref]').forEach((e) => e.removeAttribute('data-eh-ref'));
  const found = [];
  for (const e of document.querySelectorAll(selector)) {
    if (found.length >= limit) break;
    if (!visible(e)) continue;
    const ref = found.length + 1;
    e.setAttribute('data-eh-ref', String(ref));
    const label = (e.getAttribute('aria-label') || e.innerText || e.value || e.placeholder
      || e.getAttribute('name') || e.id || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
    found.push({ref, tag: e.tagName.toLowerCase(), type: e.getAttribute('type') || '', label});
  }
  return found;
}"""
_DESCRIBE_ELEMENT = """(e) => ({
  tag: e.tagName.toLowerCase(),
  type: (e.getAttribute('type') || '').toLowerCase(),
  autocomplete: (e.getAttribute('autocomplete') || '').toLowerCase(),
  name: [e.getAttribute('name'), e.id, e.getAttribute('placeholder'), e.getAttribute('aria-label')]
    .filter(Boolean).join(' '),
  label: (e.getAttribute('aria-label') || e.innerText || e.value || e.placeholder
    || e.getAttribute('name') || e.id || '').trim().replace(/\\s+/g, ' ').slice(0, 80),
})"""


def browser_available() -> bool:
    """True when the optional Playwright package is installed."""
    return importlib.util.find_spec("playwright") is not None


def parse_ports(text: str) -> frozenset[int]:
    """`3000,5173` as a set of valid ports; anything else is ignored."""
    ports = set()
    for part in text.split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= 65535:
            ports.add(int(part))
    return frozenset(ports)


def _bare_host(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    return parts.scheme.lower(), (parts.hostname or "").lower(), parts.port


@dataclass
class UrlPolicy:
    """Which addresses the browser may reach: public ones, plus local ports the user listed."""

    local_ports: frozenset[int] = frozenset()
    resolver: Callable[[str, int], list[str]] = _resolve

    def __post_init__(self) -> None:
        self._public: dict[str, bool] = {}

    def _lookup(self, host: str, port: int) -> list[str]:
        # An address written out needs no lookup, so no answer can make it look public.
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            return self.resolver(host, port)

    def is_local(self, url: str) -> bool:
        return _bare_host(url)[1] in _LOCAL_HOSTS

    def _default_port(self, scheme: str, port: int | None) -> int:
        return port or (443 if scheme in {"https", "wss"} else 80)

    def request_allowed(self, url: str) -> bool:
        """Applied to every request a page makes, including redirects and page resources."""
        try:
            scheme, host, port = _bare_host(url)
        except ValueError:
            return False
        if scheme in _FREE_SCHEMES:
            return True
        if scheme not in {"http", "https", "ws", "wss"} or not host:
            return False
        if host in _LOCAL_HOSTS:
            return self._default_port(scheme, port) in self.local_ports
        if host not in self._public:
            try:
                addresses = self._lookup(host, self._default_port(scheme, port))
                self._public[host] = bool(addresses) and all(_is_public(a) for a in addresses)
            except (OSError, ValueError):
                self._public[host] = False
        return self._public[host]

    def check_open(self, url: str) -> str | None:
        """Why the model may not open this address, or None if it may."""
        if len(url) > 2048 or any(not c.isprintable() or c == " " for c in url):
            return "the address is invalid"
        try:
            scheme, host, port = _bare_host(url)
        except ValueError:
            return "the address is invalid"
        if scheme not in {"http", "https"} or not host:
            return "only http and https addresses can be opened"
        parts = urlsplit(url)
        if parts.username or parts.password:
            return "the address must not contain a login"
        if host in _LOCAL_HOSTS:
            wanted = self._default_port(scheme, port)
            if wanted in self.local_ports:
                return None
            return (
                f"local port {wanted} is not allowed. The user can add it with "
                f"edge-harness --set browser_local_ports={wanted}"
            )
        try:
            validate_url(url, self._lookup)
        except WebError as exc:
            return str(exc)
        return None


def sensitive_field(element: dict[str, str]) -> bool:
    """True for a field that should only ever be filled in by the user."""
    if element.get("type") in {"password", "hidden", "file"}:
        return True
    if _SENSITIVE_AUTOCOMPLETE.match(element.get("autocomplete", "")):
        return True
    return bool(_SENSITIVE_NAME.search(element.get("name", "")))


class _Worker:
    """Runs functions on one thread, in order."""

    def __init__(self) -> None:
        self.jobs: queue.Queue[Any] = queue.Queue()
        self.stopped = False
        self.thread = threading.Thread(target=self._run, name="edge-browser", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None:
                return
            function, done, result = job
            try:
                result["value"] = function()
            except BaseException as exc:  # noqa: BLE001 - handed to the waiting caller
                result["error"] = exc
            done.set()

    def call(self, function: Callable[[], Any]) -> Any:
        # After stop nothing reads the queue, so waiting would never end.
        if self.stopped:
            raise WebError("the browser is closed")
        done = threading.Event()
        result: dict[str, Any] = {}
        self.jobs.put((function, done, result))
        done.wait()
        if "error" in result:
            raise result["error"]
        return result.get("value")

    def stop(self) -> None:
        self.stopped = True
        self.jobs.put(None)


class BrowserSession:
    """One throwaway browser, started on first use and gone when the chat ends."""

    def __init__(self, policy: UrlPolicy, shots_dir: Path, headed: bool = False) -> None:
        self.policy = policy
        self.shots_dir = shots_dir
        self.headed = headed
        self._worker: _Worker | None = None
        self.playwright: Any = None
        self.browser: Any = None
        self.context: Any = None
        self.page: Any = None
        self.console: list[str] = []
        self.blocked: list[str] = []
        self.shots = 0
        atexit.register(self.close)

    @property
    def worker(self) -> _Worker:
        # Started on first use, so a chat that never opens a page starts nothing.
        if self._worker is None:
            self._worker = _Worker()
        return self._worker

    def _guard(self, route: Any) -> None:
        url = route.request.url
        if self.policy.request_allowed(url):
            route.continue_()
        else:
            self.blocked.append(url[:200])
            route.abort()

    def _guard_socket(self, socket: Any) -> None:
        if self.policy.request_allowed(socket.url):
            socket.connect_to_server()
        else:
            self.blocked.append(socket.url[:200])
            socket.close()

    def _start(self) -> None:
        if self.page is not None:
            return
        from playwright.sync_api import sync_playwright

        self.playwright = sync_playwright().start()
        # Playwright's own Chromium with a temporary profile, never the user's browser or logins.
        self.browser = self.playwright.chromium.launch(headless=not self.headed)
        self.context = self.browser.new_context(
            accept_downloads=False,
            service_workers="block",
            viewport={"width": 1280, "height": 800},
        )
        self.context.route("**/*", self._guard)
        self.context.route_web_socket(re.compile(".*"), self._guard_socket)
        self.page = self.context.new_page()
        self.page.set_default_timeout(ACTION_TIMEOUT_MS)
        self.page.on("console", self._on_console)
        self.page.on("pageerror", lambda error: self._remember(f"page error: {error}"))
        self.page.on("dialog", lambda dialog: dialog.dismiss())

    def _remember(self, line: str) -> None:
        self.console.append(line[:500])
        del self.console[:-MAX_CONSOLE_LINES]

    def _on_console(self, message: Any) -> None:
        if message.type in {"error", "warning"}:
            self._remember(f"{message.type}: {message.text}")

    def open(self, url: str) -> str:
        def work() -> str:
            self._start()
            self.console.clear()
            self.blocked.clear()
            response = self.page.goto(
                url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS
            )
            with contextlib.suppress(Exception):
                self.page.wait_for_load_state("networkidle", timeout=3000)
            status = response.status if response is not None else "none"
            return f"Opened {self.page.url} (HTTP {status})\n" + self._read()

        return self.worker.call(work)

    def _read(self) -> str:
        elements = self.page.evaluate(_LIST_ELEMENTS, MAX_ELEMENTS)
        rows = [
            f"[{e['ref']}] {e['tag']}{'/' + e['type'] if e['type'] else ''}: {e['label']}"
            for e in elements
        ]
        text = self.page.inner_text("body")
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + "\n[Page truncated.]"
        sections = [
            UNTRUSTED,
            f"Address: {self.page.url}",
            f"Title: {self.page.title()}",
            "Text:\n" + text,
            "Elements (use the number to click or type):\n" + ("\n".join(rows) or "none"),
        ]
        if self.blocked:
            sections.append(f"Blocked {len(self.blocked)} requests to addresses not allowed.")
        return redact_secrets("\n\n".join(sections))

    def read(self) -> str:
        return self.worker.call(self._read_open)

    def _read_open(self) -> str:
        if self.page is None:
            raise WebError("no page is open; use browser_open first")
        return self._read()

    def _element(self, ref: int) -> Any:
        if self.page is None:
            raise WebError("no page is open; use browser_open first")
        element = self.page.query_selector(f'[data-eh-ref="{int(ref)}"]')
        if element is None:
            raise WebError(f"no element {ref} on this page; read the page again")
        return element

    def describe(self, ref: int) -> dict[str, str]:
        """The element as the page reports it, for the approval prompt and the field check."""

        def work() -> dict[str, str]:
            return self._element(ref).evaluate(_DESCRIBE_ELEMENT)

        return self.worker.call(work)

    def host(self) -> str:
        return self.worker.call(lambda: urlsplit(self.page.url).hostname or "" if self.page else "")

    def _settle(self) -> str:
        with contextlib.suppress(Exception):
            self.page.wait_for_load_state("networkidle", timeout=3000)
        return self._read()

    def click(self, ref: int) -> str:
        def work() -> str:
            self._element(ref).click(timeout=ACTION_TIMEOUT_MS)
            return "Clicked.\n" + self._settle()

        return self.worker.call(work)

    def type_text(self, ref: int, text: str) -> str:
        def work() -> str:
            self._element(ref).fill(text, timeout=ACTION_TIMEOUT_MS)
            return "Typed.\n" + self._settle()

        return self.worker.call(work)

    def press(self, key: str) -> str:
        def work() -> str:
            if self.page is None:
                raise WebError("no page is open; use browser_open first")
            self.page.keyboard.press(key)
            return "Pressed.\n" + self._settle()

        return self.worker.call(work)

    def screenshot(self) -> Path:
        def work() -> Path:
            if self.page is None:
                raise WebError("no page is open; use browser_open first")
            self.shots_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
            self.shots += 1
            target = self.shots_dir / f"browser-{id(self):x}-{self.shots}.jpg"
            self.page.screenshot(path=str(target), type="jpeg", quality=70)
            return target

        return self.worker.call(work)

    def console_lines(self) -> str:
        def work() -> str:
            lines = [*self.console, *(f"blocked: {url}" for url in self.blocked)]
            return redact_secrets("\n".join(lines)) or "No errors or warnings."

        return UNTRUSTED + "\n" + self.worker.call(work)

    def close(self) -> None:
        def work() -> None:
            for closer in (self.context, self.browser):
                with contextlib.suppress(Exception):
                    if closer is not None:
                        closer.close()
            with contextlib.suppress(Exception):
                if self.playwright is not None:
                    self.playwright.stop()
            self.page = self.context = self.browser = self.playwright = None

        worker = self._worker
        if worker is not None and not worker.stopped:
            with contextlib.suppress(Exception):
                worker.call(work)
            worker.stop()
        atexit.unregister(self.close)


def _is_later_action(tool_context: ToolContext) -> bool:
    """Only the first page-changing call in a response runs, because order matters."""
    messages = tool_context.agent.messages
    if not isinstance(messages, list) or not messages:
        return False
    calls = [
        block["toolUse"].get("toolUseId")
        for block in messages[-1].get("content", [])
        if "toolUse" in block and block["toolUse"].get("name") in _ORDERED_TOOLS
    ]
    return bool(calls) and tool_context.tool_use.get("toolUseId") != calls[0]


_LATER = "Not run: only the first browser action in a response runs. Read its result, then send the next."


def _failure(action: str, exc: Exception) -> str:
    if isinstance(exc, WebError):
        return f"{action} failed: {exc}"
    if "Executable doesn't exist" in str(exc):
        return f"{action} failed: {INSTALL_HINT}"
    # Page and provider text stays out of errors; the kind of failure is enough to act on.
    return f"{action} failed ({type(exc).__name__})."


def build_browser_tools(
    session: BrowserSession,
    review_open: Callable[[str], bool],
    review_action: Callable[[str, str], bool],
    approval_gate: threading.Lock,
    vision: Callable[[], bool] = lambda: False,
    notify: Callable[[str], None] = lambda text: None,
) -> list[Any]:
    """The browser tools. The model gets no way to run scripts in a page."""

    @tool(context=True)
    def browser_open(url: str, tool_context: ToolContext) -> str:
        """Open a page in a throwaway browser with no logins, and return its text and elements.

        The user approves each site. Use web_fetch instead for a plain page that needs no scripts.

        Args:
            url: A full http(s) address. Local addresses work only on ports the user allowed.
        """
        if _is_later_action(tool_context):
            return _LATER
        problem = session.policy.check_open(url)
        if problem:
            return f"Not opened: {problem}."
        with approval_gate:
            if not review_open(url):
                return "Not opened: the user did not approve it."
        try:
            return session.open(url)
        except Exception as exc:  # noqa: BLE001 - see _failure
            return _failure("Open", exc)

    @tool
    def browser_read() -> str:
        """Read the open page again: its address, text and numbered elements."""
        try:
            return session.read()
        except Exception as exc:  # noqa: BLE001 - see _failure
            return _failure("Read", exc)

    @tool(context=True)
    def browser_click(element: int, tool_context: ToolContext) -> str:
        """Click a numbered element from the latest page reading. The user approves each click.

        Args:
            element: The element's number.
        """
        if _is_later_action(tool_context):
            return _LATER
        try:
            found = session.describe(element)
            host = session.host()
        except Exception as exc:  # noqa: BLE001 - see _failure
            return _failure("Click", exc)
        summary = f"Click {found.get('tag')} {found.get('label', '')!r} on {host}"
        with approval_gate:
            if not review_action(summary, host):
                return "Not clicked: the user did not approve it."
        try:
            return session.click(element)
        except Exception as exc:  # noqa: BLE001 - see _failure
            return _failure("Click", exc)

    @tool(context=True)
    def browser_type(element: int, text: str, tool_context: ToolContext) -> str:
        """Type into a numbered field. The user approves the text. Password, payment and similar
        fields are refused, so ask the user to fill those in themselves.

        Args:
            element: The field's number.
            text: What to type. Never put repository content or secrets in it.
        """
        if _is_later_action(tool_context):
            return _LATER
        if len(text) > MAX_TYPED_CHARS or any(not c.isprintable() and c != "\n" for c in text):
            return "Not typed: the text is too long or holds hidden characters."
        try:
            found = session.describe(element)
            host = session.host()
        except Exception as exc:  # noqa: BLE001 - see _failure
            return _failure("Type", exc)
        if sensitive_field(found):
            notify("browser: refused to type into a sensitive field")
            return "Not typed: this is a password, payment or similar field. Ask the user to do it."
        summary = f"Type {text!r} into {found.get('tag')} {found.get('label', '')!r} on {host}"
        with approval_gate:
            if not review_action(summary, host):
                return "Not typed: the user did not approve it."
        try:
            return session.type_text(element, text)
        except Exception as exc:  # noqa: BLE001 - see _failure
            return _failure("Type", exc)

    @tool(context=True)
    def browser_press(key: str, tool_context: ToolContext) -> str:
        """Press Enter, Tab, Escape or an arrow key on the open page. The user approves each press.

        Args:
            key: One of Enter, Tab, Escape, ArrowUp, ArrowDown, ArrowLeft or ArrowRight.
        """
        if _is_later_action(tool_context):
            return _LATER
        if key not in _ALLOWED_KEYS:
            return f"Not pressed: use one of {', '.join(sorted(_ALLOWED_KEYS))}."
        try:
            host = session.host()
            with approval_gate:
                if not review_action(f"Press {key} on {host}", host):
                    return "Not pressed: the user did not approve it."
            return session.press(key)
        except Exception as exc:  # noqa: BLE001 - see _failure
            return _failure("Press", exc)

    @tool
    def browser_screenshot() -> Any:
        """Take a screenshot of the open page. Models that can read images also see it."""
        try:
            path = session.screenshot()
        except Exception as exc:  # noqa: BLE001 - see _failure
            return _failure("Screenshot", exc)
        notify(f"browser: screenshot saved to {path}")
        note = f"{UNTRUSTED}\nScreenshot saved to {path}."
        if not vision():
            return note + " This model cannot read images, so use browser_read for the text."
        return {
            "status": "success",
            "content": [
                {"text": note},
                {
                    "image": {
                        "format": "jpeg",
                        "source": {"bytes": path.read_bytes()},
                    }
                },
            ],
        }

    @tool
    def browser_console() -> str:
        """List the open page's console errors and warnings, and any blocked requests."""
        try:
            return session.console_lines()
        except Exception as exc:  # noqa: BLE001 - see _failure
            return _failure("Console", exc)

    return [
        browser_open,
        browser_read,
        browser_click,
        browser_type,
        browser_press,
        browser_screenshot,
        browser_console,
    ]
