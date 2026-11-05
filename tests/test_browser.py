import http.server
import os
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from code_harness.browser import (
    BrowserSession,
    UrlPolicy,
    _failure,
    build_browser_tools,
    parse_ports,
    sensitive_field,
)
from code_harness.web import UNTRUSTED, WebError

PUBLIC = "93.184.216.34"


def resolver(host: str, port: int) -> list[str]:
    return {"lan.example": ["192.168.1.5"], "meta.example": ["169.254.169.254"]}.get(host, [PUBLIC])


def policy(*ports: int) -> UrlPolicy:
    return UrlPolicy(frozenset(ports), resolver)


def test_parse_ports_ignores_rubbish() -> None:
    assert parse_ports("3000, 5173,abc,0,70000,") == {3000, 5173}
    assert parse_ports("") == frozenset()


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "http://example.com/a?b=c",
        "http://localhost:3000/",
        "http://127.0.0.1:3000/x",
    ],
)
def test_allowed_addresses_open(url: str) -> None:
    assert policy(3000).check_open(url) is None


@pytest.mark.parametrize(
    "url, reason",
    [
        ("http://localhost:8080/", "not allowed"),
        ("http://127.0.0.1/", "not allowed"),
        ("http://[::1]:9000/", "not allowed"),
        ("http://lan.example/", "public"),
        ("http://169.254.169.254/latest/meta-data/", "public"),
        ("http://meta.example/", "public"),
        ("http://192.168.1.1/", "public"),
        ("file:///C:/Windows/win.ini", "http"),
        ("chrome://settings", "http"),
        ("javascript:alert(1)", "http"),
        ("https://user:pw@example.com/", "login"),
        ("https://example.com/a b", "invalid"),
        ("https://example.com/" + "a" * 3000, "invalid"),
        ("https://example.com:notaport/", "invalid"),
    ],
)
def test_other_addresses_are_refused(url: str, reason: str) -> None:
    problem = policy(3000).check_open(url)
    assert problem is not None and reason in problem


@pytest.mark.parametrize(
    "url, allowed",
    [
        ("https://example.com/app.js", True),
        ("wss://example.com/socket", True),
        ("data:text/plain,hi", True),
        ("about:blank", True),
        ("blob:https://example.com/1", True),
        ("http://localhost:3000/api", True),
        ("ws://localhost:3000/hmr", True),
        ("http://localhost:6379/", False),
        ("http://127.0.0.1:22/", False),
        ("http://[::1]:5432/", False),
        ("http://192.168.1.1/admin", False),
        ("http://lan.example/", False),
        ("http://169.254.169.254/latest/meta-data/", False),
        ("file:///etc/passwd", False),
        ("ftp://example.com/", False),
        ("http://", False),
        ("http://example.com:bad/", False),
    ],
)
def test_every_request_a_page_makes_is_checked(url: str, allowed: bool) -> None:
    assert policy(3000).request_allowed(url) is allowed


def test_failed_lookup_is_refused_and_results_are_cached() -> None:
    calls: list[str] = []

    def failing(host: str, port: int) -> list[str]:
        calls.append(host)
        raise OSError

    rules = UrlPolicy(frozenset(), failing)
    assert not rules.request_allowed("https://x.example/1")
    assert not rules.request_allowed("https://x.example/2")
    assert calls == ["x.example"]


@pytest.mark.parametrize(
    "element",
    [
        {"type": "password"},
        {"type": "hidden"},
        {"type": "file"},
        {"autocomplete": "current-password"},
        {"autocomplete": "new-password"},
        {"autocomplete": "one-time-code"},
        {"autocomplete": "cc-number"},
        {"autocomplete": "section-a cc-csc"},
        {"name": "user_password"},
        {"name": "passcode"},
        {"name": "api secret"},
        {"name": "card-number"},
        {"name": "cardnumber"},
        {"name": "cvv"},
        {"name": "otp"},
    ],
)
def test_sensitive_fields_are_recognized(element: dict[str, str]) -> None:
    assert sensitive_field(element)


@pytest.mark.parametrize(
    "element",
    [
        {"type": "text", "name": "search"},
        {"type": "email", "name": "email", "autocomplete": "email"},
        {"type": "", "name": "compass"},
        {"name": "topic"},
        {},
    ],
)
def test_ordinary_fields_are_allowed(element: dict[str, str]) -> None:
    assert not sensitive_field(element)


def test_failures_hide_page_text_and_explain_a_missing_browser() -> None:
    assert "secret" not in _failure("Open", RuntimeError("secret page text"))
    assert "playwright install chromium" in _failure(
        "Open", RuntimeError("Executable doesn't exist at x")
    )
    assert "no page" in _failure("Read", WebError("no page is open"))


class FakeSession:
    def __init__(self, element: dict[str, str] | None = None) -> None:
        self.policy = policy(3000)
        self.element = element or {"tag": "button", "label": "Save"}
        self.done: list[Any] = []

    def open(self, url: str) -> str:
        self.done.append(("open", url))
        return "opened"

    def describe(self, ref: int) -> dict[str, str]:
        return self.element

    def host(self) -> str:
        return "localhost"

    def click(self, ref: int) -> str:
        self.done.append(("click", ref))
        return "clicked"

    def type_text(self, ref: int, text: str) -> str:
        self.done.append(("type", ref, text))
        return "typed"

    def press(self, key: str) -> str:
        self.done.append(("press", key))
        return "pressed"


def _tools(session: Any, approve: bool = True) -> tuple[dict[str, Any], MagicMock, MagicMock]:
    review_open, review_action = MagicMock(return_value=approve), MagicMock(return_value=approve)
    built = build_browser_tools(session, review_open, review_action, threading.Lock())
    return {t.tool_name: t._tool_func for t in built}, review_open, review_action


def _context() -> MagicMock:
    context = MagicMock()
    context.agent.messages = []
    return context


def test_refused_address_never_asks_or_opens() -> None:
    session = FakeSession()
    tools, review_open, _ = _tools(session)
    assert "not allowed" in tools["browser_open"]("http://localhost:6379/", tool_context=_context())
    review_open.assert_not_called()
    assert session.done == []


def test_rejected_actions_do_nothing() -> None:
    session = FakeSession()
    tools, _, _ = _tools(session, approve=False)
    context = _context()
    assert "did not approve" in tools["browser_open"]("https://example.com/", tool_context=context)
    assert "did not approve" in tools["browser_click"](1, tool_context=context)
    assert "did not approve" in tools["browser_type"](1, "x", tool_context=context)
    assert "did not approve" in tools["browser_press"]("Enter", tool_context=context)
    assert session.done == []


def test_approval_shows_the_exact_action() -> None:
    session = FakeSession({"tag": "input", "label": "Search"})
    tools, _, review_action = _tools(session)
    tools["browser_type"](2, "rivers", tool_context=_context())
    summary, host = review_action.call_args.args
    assert "rivers" in summary and "Search" in summary and host == "localhost"
    assert session.done == [("type", 2, "rivers")]


def test_password_field_is_refused_before_asking() -> None:
    session = FakeSession({"tag": "input", "type": "password", "label": "Password"})
    tools, _, review_action = _tools(session)
    assert "Ask the user" in tools["browser_type"](1, "hunter2", tool_context=_context())
    review_action.assert_not_called()
    assert session.done == []


@pytest.mark.parametrize("text", ["x" * 3000, "bad\x1b[2J", "hidden\u202eword"])
def test_odd_typed_text_is_refused(text: str) -> None:
    session = FakeSession()
    tools, _, review_action = _tools(session)
    assert "Not typed" in tools["browser_type"](1, text, tool_context=_context())
    review_action.assert_not_called()


def test_only_listed_keys_can_be_pressed() -> None:
    session = FakeSession()
    tools, _, review_action = _tools(session)
    assert "use one of" in tools["browser_press"]("Control+A", tool_context=_context())
    review_action.assert_not_called()


def test_only_the_first_action_of_a_response_runs() -> None:
    session = FakeSession()
    tools, _, review_action = _tools(session)
    context = _context()
    context.agent.messages = [
        {
            "role": "assistant",
            "content": [
                {"toolUse": {"toolUseId": "a", "name": "browser_click"}},
                {"toolUse": {"toolUseId": "b", "name": "browser_click"}},
                {"toolUse": {"toolUseId": "c", "name": "browser_read"}},
            ],
        }
    ]
    context.tool_use = {"toolUseId": "b"}
    assert "only the first" in tools["browser_click"](2, tool_context=context)
    review_action.assert_not_called()
    context.tool_use = {"toolUseId": "a"}
    assert tools["browser_click"](1, tool_context=context) == "clicked"


def test_no_tool_can_run_scripts_in_a_page() -> None:
    tools, _, _ = _tools(FakeSession())
    assert not any("eval" in name or "script" in name for name in tools)


PAGES = {
    "/": """<html><head><title>Test shop</title></head><body>
        <h1>Welcome</h1>
        <p style="display:none">Hidden note</p>
        <button onclick="document.getElementById('out').innerText='clicked ok'">Press me</button>
        <div id="out">waiting</div>
        <input name="search" id="s" oninput="document.getElementById('echo').innerText=this.value">
        <div id="echo"></div>
        <input type="password" name="pw">
        <a href="/redirect-to-other">Go</a>
        <img src="http://127.0.0.1:{blocked}/steal.png">
        <script>console.error('broken thing'); fetch('http://127.0.0.1:{blocked}/secret')</script>
        </body></html>""",
    "/page2": "<html><title>Second</title><body>Second page</body></html>",
}


def _server(hits: list[str]) -> http.server.ThreadingHTTPServer:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(self.server.body(self.path).encode())  # type: ignore[attr-defined]

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


live = pytest.mark.skipif(
    os.environ.get("HARNESS_TEST_BROWSER") != "1", reason="real browser tests are opt-in"
)


@pytest.fixture
def real(tmp_path: Path):
    blocked_hits: list[str] = []
    allowed_hits: list[str] = []
    blocked = _server(blocked_hits)
    allowed = _server(allowed_hits)
    blocked_port = blocked.server_address[1]
    allowed.body = lambda path: PAGES.get(path, "<html>missing</html>").replace(  # type: ignore[attr-defined]
        "{blocked}", str(blocked_port)
    )
    blocked.body = lambda path: "SECRET"  # type: ignore[attr-defined]
    port = allowed.server_address[1]
    session = BrowserSession(UrlPolicy(frozenset({port}), resolver), tmp_path / "shots")
    yield session, f"http://127.0.0.1:{port}", blocked_hits, port
    session.close()
    allowed.shutdown()
    blocked.shutdown()


@live
def test_real_browser_reads_clicks_types_and_blocks(real) -> None:
    session, base, blocked_hits, _ = real
    page = session.open(base + "/")
    assert UNTRUSTED in page and "Welcome" in page and "Title: Test shop" in page
    assert "Hidden note" not in page
    assert "button: Press me" in page

    # A page cannot reach a local port the user did not list, even with scripts or images.
    assert blocked_hits == []
    assert "Blocked" in page
    console = session.console_lines()
    assert "broken thing" in console and "blocked:" in console

    number = int(page.split("button: Press me")[0].rsplit("[", 1)[1].split("]")[0])
    assert session.describe(number)["label"] == "Press me"
    assert "clicked ok" in session.click(number)

    fresh = session.read()
    field = int(fresh.split("input: s")[0].rsplit("[", 1)[1].split("]")[0])
    assert "rivers" in session.type_text(field, "rivers")

    fresh = session.read()
    secret = int(fresh.split("input/password")[0].rsplit("[", 1)[1].split("]")[0])
    assert sensitive_field(session.describe(secret))

    shot = session.screenshot()
    assert shot.exists() and shot.read_bytes()[:2] == b"\xff\xd8"


@live
def test_real_browser_has_no_saved_state(real, tmp_path: Path) -> None:
    session, base, _, port = real
    session.open(base + "/page2")
    assert session.worker.call(lambda: session.context.cookies()) == []
    assert session.worker.call(lambda: session.browser.browser_type.name) == "chromium"
    other = BrowserSession(UrlPolicy(frozenset({port}), resolver), tmp_path / "other")
    try:
        other.open(base + "/page2")
        session.worker.call(
            lambda: session.context.add_cookies([{"name": "a", "value": "1", "url": base}])
        )
        assert other.worker.call(lambda: other.context.cookies()) == []
    finally:
        other.close()


@live
def test_real_browser_refuses_file_navigation(real) -> None:
    session, base, _, _ = real
    session.open(base + "/page2")

    def go() -> str:
        try:
            session.page.goto("file:///C:/Windows/win.ini", timeout=5000)
        except Exception as exc:  # noqa: BLE001 - the refusal is what is being checked
            return type(exc).__name__
        return session.page.inner_text("body")

    assert "fonts" not in session.worker.call(go).lower()


@live
def test_real_browser_closes_cleanly_and_reports_no_page(tmp_path: Path) -> None:
    session = BrowserSession(UrlPolicy(frozenset(), resolver), tmp_path)
    with pytest.raises(WebError, match="no page"):
        session.read()
    session.close()
    session.close()


def test_closing_twice_and_using_after_close_never_hangs(tmp_path: Path) -> None:
    session = BrowserSession(UrlPolicy(frozenset(), resolver), tmp_path)
    session.close()  # Never started: nothing to do.
    assert session._worker is None
    with pytest.raises(WebError, match="no page"):
        session.read()
    session.close()
    session.close()
    with pytest.raises(WebError, match="closed"):
        session.read()


def _action(summary: str, host: str, reply: str, session=None) -> tuple[bool, list[str]]:
    from code_harness.approvals import review_browser_action

    prompts: list[str] = []

    def read(text: str) -> str:
        prompts.append(text)
        return reply

    return (
        review_browser_action(
            summary,
            host,
            interactive=True,
            display=lambda t: None,
            read_reply=read,
            session=session,
        ),
        prompts,
    )


def test_standing_approval_is_only_offered_on_localhost() -> None:
    from code_harness.approvals import SessionApprovals

    session = SessionApprovals()
    approved, prompts = _action("Click x", "example.com", "a", session)
    assert not approved and "[a]ll" not in prompts[0]
    assert not session.browser_hosts

    approved, prompts = _action("Click x", "localhost", "a", session)
    assert approved and "[a]ll on localhost" in prompts[0]
    approved, prompts = _action("Click y", "localhost", "no", session)
    assert approved and prompts == []
    assert not _action("Click y", "127.0.0.1", "no", session)[0]
    session.reset()
    assert not _action("Click y", "localhost", "no", session)[0]


def test_edit_and_command_approvals_do_not_cover_the_browser() -> None:
    from code_harness.approvals import SessionApprovals

    session = SessionApprovals(edits=True, commands=True, web_hosts={"example.com"})
    assert not _action("Click x", "example.com", "no", session)[0]


def test_browser_actions_are_refused_without_a_terminal() -> None:
    from code_harness.approvals import SessionApprovals, review_browser_action

    assert not review_browser_action(
        "Click x",
        "localhost",
        interactive=False,
        display=print,
        read_reply=lambda t: "yes",
        session=SessionApprovals(browser_hosts={"localhost"}),
    )


def _agent(tmp_path: Path, script: Any = None, **kwargs: Any) -> Any:
    from test_subagents import Script

    from code_harness.agent import create_agent
    from code_harness.config import Settings

    settings = Settings(workspace=tmp_path, **kwargs.pop("settings", {}))
    return create_agent(settings, model=(script or Script(main=[])).model(), **kwargs)


BROWSER_TOOLS = {
    "browser_open",
    "browser_read",
    "browser_click",
    "browser_type",
    "browser_press",
    "browser_screenshot",
    "browser_console",
}


def test_tools_need_approval_paths_playwright_and_the_setting(tmp_path: Path, monkeypatch) -> None:
    from code_harness import agent as agent_module

    monkeypatch.setattr(agent_module, "browser_available", lambda: True)
    review = {"review_browser": MagicMock(), "review_web": MagicMock()}
    assert BROWSER_TOOLS <= set(_agent(tmp_path, **review).tool_registry.registry)
    assert "untrusted" in str(_agent(tmp_path, **review).system_prompt)

    assert not BROWSER_TOOLS & set(_agent(tmp_path).tool_registry.registry)
    only_one = {"review_browser": MagicMock()}
    assert not BROWSER_TOOLS & set(_agent(tmp_path, **only_one).tool_registry.registry)
    off = _agent(tmp_path, settings={"harness_enable_browser": False}, **review)
    assert not BROWSER_TOOLS & set(off.tool_registry.registry)

    monkeypatch.setattr(agent_module, "browser_available", lambda: False)
    assert not BROWSER_TOOLS & set(_agent(tmp_path, **review).tool_registry.registry)


def test_subagents_never_get_browser_tools(tmp_path: Path, monkeypatch) -> None:
    from test_subagents import Script, run_turn, spawn

    from code_harness import agent as agent_module

    monkeypatch.setattr(agent_module, "browser_available", lambda: True)
    roles = ["explore", "review", "edit"]
    script = Script(main=[[spawn(role) for role in roles], "ok"], sub=["a", "b", "c"])
    run_turn(
        _agent(
            tmp_path,
            script,
            review=MagicMock(return_value=False),
            review_browser=MagicMock(),
            review_web=MagicMock(),
        )
    )
    assert len(script.sub_tools) == 3
    assert not any(BROWSER_TOOLS & names for names in script.sub_tools)


def test_screenshot_reaches_only_a_model_that_can_read_images(tmp_path: Path) -> None:
    shot = tmp_path / "a.jpg"
    shot.write_bytes(b"\xff\xd8data")

    class Shots(FakeSession):
        def screenshot(self) -> Path:
            return shot

    def result(vision: bool) -> Any:
        built = build_browser_tools(
            Shots(), MagicMock(), MagicMock(), threading.Lock(), lambda: vision
        )
        return {t.tool_name: t._tool_func for t in built}["browser_screenshot"]()

    assert "cannot read images" in result(False)
    content = result(True)["content"]
    assert content[1]["image"]["source"]["bytes"] == b"\xff\xd8data"
    assert UNTRUSTED in content[0]["text"]


def test_port_setting_is_validated() -> None:
    from code_harness.launcher import valid_setting

    assert valid_setting("browser_local_ports", "3000,5173")
    for bad in ["3000, 5173", "abc", "1", "3000,", "3000;rm"]:
        assert not valid_setting("browser_local_ports", bad)


def test_browser_visit_prompt_says_what_it_is() -> None:
    from code_harness.approvals import review_fetch

    shown: list[str] = []
    asked: list[str] = []

    def read(text: str) -> str:
        asked.append(text)
        return "no"

    review_fetch(
        "https://example.com/",
        interactive=True,
        display=shown.append,
        read_reply=read,
        in_browser=True,
    )
    assert "browser visit" in shown[0] and "Open this page in the browser" in asked[0]
    review_fetch("https://example.com/", interactive=True, display=shown.append, read_reply=read)
    assert "web fetch" in shown[1] and "Fetch this page" in asked[1]
