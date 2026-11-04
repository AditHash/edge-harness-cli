import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from test_subagents import Script, run_turn, spawn

from code_harness import web
from code_harness.agent import create_agent
from code_harness.approvals import SessionApprovals, review_fetch, review_search
from code_harness.config import Settings
from code_harness.web import WebError, WebReview, build_web_tools, fetch_page, validate_url

PUBLIC = "93.184.216.34"


def public(host: str, port: int) -> list[str]:
    return [PUBLIC]


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1", "169.254.169.254", "::1", "fe80::1"],
)
def test_non_public_addresses_are_refused(address: str) -> None:
    with pytest.raises(WebError, match="not a public"):
        validate_url("https://example.com/", lambda host, port: [address])


def test_one_private_address_among_public_ones_is_refused() -> None:
    with pytest.raises(WebError, match="not a public"):
        validate_url("https://example.com/", lambda host, port: [PUBLIC, "127.0.0.1"])


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "https://user:pw@example.com/",
        "https://example.com/a b",
        "https://example.com/\x1b[2J",
        "https://",
        "https://example.com:notaport/",
        "https://example.com/" + "a" * 3000,
    ],
)
def test_malformed_urls_are_refused(url: str) -> None:
    with pytest.raises(WebError):
        validate_url(url, public)


class FakeResponse:
    def __init__(self, status: int, headers: dict[str, str], body: bytes = b"") -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self.headers.get(name, default)

    def read(self, size: int) -> bytes:
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk


def serve(monkeypatch: pytest.MonkeyPatch, responses: list[FakeResponse]) -> list[tuple[str, Any]]:
    opened: list[tuple[str, Any]] = []

    def fake_open(url: str, addresses: list[str], timeout: float) -> tuple[Any, Any]:
        opened.append((url, addresses))
        return MagicMock(), responses.pop(0)

    monkeypatch.setattr(web, "_open", fake_open)
    return opened


def test_html_becomes_text_without_scripts(monkeypatch: pytest.MonkeyPatch) -> None:
    page = (
        b"<html><!-- hidden --><script>evil()</script><p>Hello &amp; <b>bye</b>\x1b[2J</p></html>"
    )
    serve(monkeypatch, [FakeResponse(200, {"Content-Type": "text/html; charset=utf-8"}, page)])
    url, text = fetch_page("https://example.com/#frag", public)
    assert url == "https://example.com/"
    assert "Hello & bye" in text and "evil" not in text and "\x1b" not in text
    assert "hidden" not in text


def test_redirect_to_private_address_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    redirect = {"Location": "http://169.254.169.254/latest/meta-data/"}
    opened = serve(monkeypatch, [FakeResponse(302, redirect)])

    def resolver(host: str, port: int) -> list[str]:
        return ["169.254.169.254"] if host == "169.254.169.254" else [PUBLIC]

    with pytest.raises(WebError, match="not a public"):
        fetch_page("https://example.com/", resolver)
    assert [url for url, _ in opened] == ["https://example.com/"]


def test_relative_redirect_is_followed_and_loops_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    ok = FakeResponse(200, {"Content-Type": "text/plain"}, b"done")
    serve(monkeypatch, [FakeResponse(301, {"Location": "/next"}), ok])
    assert fetch_page("https://example.com/a", public) == ("https://example.com/next", "done")
    serve(monkeypatch, [FakeResponse(302, {"Location": "/a"}) for _ in range(9)])
    with pytest.raises(WebError, match="Too many"):
        fetch_page("https://example.com/a", public)


def test_binary_errors_and_size_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, [FakeResponse(200, {"Content-Type": "application/zip"}, b"PK")])
    with pytest.raises(WebError, match="text pages"):
        fetch_page("https://example.com/", public)
    serve(monkeypatch, [FakeResponse(404, {})])
    with pytest.raises(WebError, match="HTTP 404"):
        fetch_page("https://example.com/", public)
    big = b"x" * (web.MAX_BYTES + 1000)
    serve(monkeypatch, [FakeResponse(200, {"Content-Type": "text/plain"}, big)])
    _, text = fetch_page("https://example.com/", public)
    assert len(text) < web.MAX_TEXT_CHARS + 50 and text.endswith("[Page truncated.]")


@pytest.mark.parametrize("kind", [web._PinnedHTTP, web._PinnedHTTPS])
def test_connection_uses_the_checked_address_not_a_new_lookup(
    monkeypatch: pytest.MonkeyPatch, kind: Any
) -> None:
    seen: list[Any] = []

    def create_connection(address: Any, timeout: Any = None) -> Any:
        seen.append(address)
        raise OSError("stop here")

    monkeypatch.setattr(web.socket, "create_connection", create_connection)
    with pytest.raises(OSError, match="stop here"):
        kind("example.com", 443, PUBLIC, 1).connect()
    assert seen == [(PUBLIC, 443)]


def test_tls_verification_stays_on() -> None:
    import ssl

    context = web._PinnedHTTPS("example.com", 443, PUBLIC, 1).tls_context
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def _tools(search: bool = True, fetch: bool = True) -> tuple[Any, Any, Any]:
    review = WebReview(MagicMock(return_value=search), MagicMock(return_value=fetch))
    found = {t.tool_name: t._tool_func for t in build_web_tools(review, threading.Lock())}
    return found["web_search"], found["web_fetch"], review


def test_refused_requests_reach_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "_resolve", public)
    monkeypatch.setattr(web, "fetch_page", MagicMock(side_effect=AssertionError("network")))
    exa = MagicMock(side_effect=AssertionError("network"))
    monkeypatch.setattr(web, "make_exa_web_search", lambda: exa)
    search, fetch, _ = _tools(search=False, fetch=False)
    assert "did not approve" in search("python news")
    assert "not run" in search("bad\x1b[2Jquery")
    assert "not run" in fetch("file:///etc/passwd")


def test_private_url_is_refused_before_asking_the_user() -> None:
    _, fetch, review = _tools()
    assert "public address" in fetch("http://127.0.0.1:8080/secrets")
    assert "public address" in fetch("http://169.254.169.254/latest/meta-data/")
    review.fetch.assert_not_called()


def test_web_results_are_marked_untrusted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exa(query: str, max_results: int) -> str:
        return f"Sources: {query}"

    monkeypatch.setattr(web, "make_exa_web_search", lambda: exa)
    search, _, review = _tools()
    assert search("python news").startswith(web.UNTRUSTED)
    review.search.assert_called_once_with("python news")


def _ask(reply: str) -> dict[str, Any]:
    return {"interactive": True, "display": lambda text: None, "read_reply": lambda text: reply}


def test_standing_fetch_approval_covers_one_host_only() -> None:
    session = SessionApprovals()
    assert review_fetch("https://a.example/1", session=session, **_ask("a"))
    assert review_fetch("https://a.example/2?x=1", session=session, **_ask("no"))
    assert not review_fetch("https://b.example/", session=session, **_ask("no"))
    assert not session.searches
    assert "a.example" in session.describe()
    session.reset()
    assert not review_fetch("https://a.example/1", session=session, **_ask("no"))


def test_edit_and_command_approvals_do_not_cover_the_web() -> None:
    session = SessionApprovals(edits=True, commands=True)
    assert not review_search("q", session=session, **_ask(""))
    assert not review_fetch("https://a.example/", session=session, **_ask(""))
    assert review_search("q", session=session, **_ask("all"))
    assert session.searches and review_search("q2", session=session, **_ask("no"))


def test_web_is_refused_without_a_terminal() -> None:
    session = SessionApprovals(searches=True, web_hosts={"a.example"})
    quiet = {"interactive": False, "display": print, "read_reply": lambda text: "yes"}
    assert not review_search("q", session=session, **quiet)
    assert not review_fetch("https://a.example/", session=session, **quiet)


def test_only_explore_subagents_get_web_tools(tmp_path: Path) -> None:
    script = Script(main=[[spawn("explore"), spawn("review")], "ok"], sub=["a", "b"])
    review_web = WebReview(MagicMock(return_value=False), MagicMock(return_value=False))
    agent = create_agent(Settings(workspace=tmp_path), model=script.model(), review_web=review_web)
    assert {"web_search", "web_fetch"} <= set(agent.tool_registry.registry)
    run_turn(agent)
    with_web = [names for names in script.sub_tools if "web_fetch" in names]
    assert len(with_web) == 1 and "web_search" in with_web[0]


def test_web_needs_review_and_can_be_disabled(tmp_path: Path) -> None:
    model = Script(main=[]).model()
    agent = create_agent(Settings(workspace=tmp_path), model=model)
    assert "web_fetch" not in agent.tool_registry.registry
    review_web = WebReview(MagicMock(), MagicMock())
    settings = Settings(workspace=tmp_path, harness_enable_web=False)
    agent = create_agent(settings, model=model, review_web=review_web)
    assert "web_fetch" not in agent.tool_registry.registry
