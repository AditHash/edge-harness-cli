import asyncio
import threading
from pathlib import Path

import pytest
from rich.console import Console

from code_harness import cli, ui
from code_harness.contracts import AgentEvent
from code_harness.pricing import Price, PriceBook
from code_harness.sessions import Sessions
from code_harness.usage import SubagentLedger, UsageTracker


class Book:
    def get(self, model: str) -> Price | None:
        return Price(1.0, 2.0, 0.5, "test") if model == "known" else None


def tracker(**kwargs) -> UsageTracker:
    return UsageTracker(prices=Book(), **kwargs)


def test_each_call_is_priced_by_its_own_model() -> None:
    usage = tracker()
    assert usage.add_call("known", 1_000_000, 0, 0) == pytest.approx(1.0)
    assert usage.add_call("known", 0, 1_000_000, 2_000_000) == pytest.approx(3.0)
    assert usage.cost == pytest.approx(4.0)
    assert usage.by_model["known"]["cached"] == 2_000_000


def test_unknown_price_is_never_a_guess_or_hidden() -> None:
    usage = tracker()
    assert usage.add_call("mystery", 1000, 1000) is None
    assert usage.cost is None
    assert "Cost unknown" in usage.summary()
    usage.add_call("known", 1_000_000, 0)
    assert usage.cost == pytest.approx(1.0)
    assert "1 calls had no price" in usage.summary()


def test_calls_from_many_threads_are_all_counted() -> None:
    usage = tracker()
    threads = [
        threading.Thread(target=lambda: [usage.add_call("known", 1000, 0) for _ in range(200)])
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert usage.calls == 1600
    assert usage.cost == pytest.approx(1600 * 1000 / 1_000_000)


def test_ledger_reports_subagent_calls_as_they_finish() -> None:
    usage = tracker()
    ledger = SubagentLedger(listener=usage.add_call)
    ledger.add(1_000_000, 0, 0, "known")
    assert usage.cost == pytest.approx(1.0)
    assert ledger.drain() == (1_000_000, 0, 0)


def test_budget_warns_once_per_level_and_stops_nothing() -> None:
    usage = tracker(budget_usd=1.0)
    assert usage.budget_warning() == ""
    usage.add_call("known", 850_000, 0)
    assert usage.budget_state() == "near"
    assert "near" in usage.budget_warning()
    assert usage.budget_warning() == ""
    usage.add_call("known", 200_000, 0)
    assert "over" in usage.budget_warning()
    assert usage.budget_warning() == ""
    assert tracker().budget_state() == ""


def test_session_totals_survive_a_resume(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    sessions = Sessions(tmp_path / "s.sqlite", root)
    session_id = sessions.create()
    sessions.record_turn(session_id, "first question")
    first = tracker()
    first.add(100, 10, 5)
    first.add_call("known", 1_000_000, 0)
    sessions.record_usage(session_id, first.totals())
    sessions.record_turn(session_id, "second question")

    saved = sessions.info(session_id)
    assert saved.title == "first question" and saved.turns == 2  # Nothing else was lost.
    second = tracker()
    second.start_session(saved.usage, saved.turns)
    assert second.cost == pytest.approx(1.0) and second.input_tokens == 100
    assert second.turns == 2 and second.by_model["known"]["input"] == 1_000_000
    second.add_call("known", 1_000_000, 0)
    assert second.cost == pytest.approx(2.0)
    second.start_session()
    assert second.cost is None and second.input_tokens == 0 and not second.by_model


@pytest.mark.parametrize(
    "saved",
    [
        None,
        "x",
        {"spent": "9"},
        {"spent": True},
        {"spent": -5},
        {"models": "x"},
        {"models": {"m": {"cost": "x"}}},
    ],
)
def test_damaged_saved_usage_counts_as_nothing(saved) -> None:
    usage = tracker()
    usage.start_session(saved)
    assert usage.spent == 0
    assert usage.input_tokens == 0


def _turn(monkeypatch, events, usage, agent=None):
    recorder = Console(width=120, record=True, color_system=None)
    monkeypatch.setattr(cli, "console", recorder)

    async def fake(agent, prompt):
        for event in events:
            yield event

    monkeypatch.setattr(cli, "stream_turn", fake)
    asyncio.run(cli._show_turn(agent or object(), "x", usage))
    return recorder.export_text()


def test_turn_shows_call_turn_and_session_cost(monkeypatch) -> None:
    usage = tracker()
    usage.add_call("known", 1_000_000, 0)
    text = _turn(
        monkeypatch,
        [
            AgentEvent("call_usage", "known", 1_000_000, 0, 0),
            AgentEvent("text_delta", "done"),
            AgentEvent("usage", "", 1_000_000, 0, 0),
        ],
        usage,
    )
    assert "~$1.00" in text and "turn" in text and "~$2.00" in text and "session" in text


def test_unknown_price_shows_no_invented_amount(monkeypatch) -> None:
    text = _turn(
        monkeypatch,
        [AgentEvent("call_usage", "mystery", 1000, 10, 0), AgentEvent("usage", "", 1000, 10, 0)],
        tracker(),
    )
    assert "$" not in text


def test_turn_cut_short_keeps_tokens_and_cost(monkeypatch) -> None:
    usage = tracker()
    with pytest.raises(RuntimeError):

        async def failing(agent, prompt):
            yield AgentEvent("call_usage", "known", 1_000_000, 5, 0)
            raise RuntimeError

        monkeypatch.setattr(cli, "console", Console(record=True, color_system=None))
        monkeypatch.setattr(cli, "stream_turn", failing)
        asyncio.run(cli._show_turn(object(), "x", usage))
    assert usage.input_tokens == 1_000_000 and usage.output_tokens == 5
    assert usage.cost == pytest.approx(1.00001)


def test_budget_warning_appears_after_the_turn(monkeypatch) -> None:
    usage = tracker(budget_usd=0.5)
    text = _turn(monkeypatch, [AgentEvent("call_usage", "known", 1_000_000, 0, 0)], usage)
    assert "over your budget" in text and "Nothing was stopped" in text


def test_status_bar_shows_cost_and_budget_color() -> None:
    segments = ui.status_segments(
        model="m",
        folder="f",
        approvals="ask",
        context_used=50,
        context_limit=100,
        input_tokens=1,
        cached_tokens=0,
        output_tokens=1,
        cost="~$1.2300",
        cost_style="fg:ansired bold",
        fancy=True,
    )
    assert ("fg:ansired bold", "~$1.2300") in segments
    assert any("\u25b0" in text for _, text in segments)
    assert "cost" not in "".join(
        text
        for _, text in ui.status_segments(
            model="m",
            folder="f",
            approvals="ask",
            context_used=None,
            context_limit=None,
            input_tokens=0,
            cached_tokens=0,
            output_tokens=0,
        )
    )


def _render(renderable) -> str:
    console = Console(width=160, record=True, color_system=None)
    console.print(renderable)
    return console.export_text()


def test_cost_table_names_rate_and_source(tmp_path: Path) -> None:
    usage = UsageTracker(prices=PriceBook(tmp_path))
    usage.add_call("in.moonshotai.kimi-k3", 1000, 1000, 1000)
    usage.add_call("mystery", 1000, 1000)
    text = _render(ui.cost_table(usage))
    assert "3.3 / 0.33 / 16.5" in text and "built-in AWS snapshot" in text
    assert "mystery" in text and "unknown" in text
    assert "No model calls" in _render(ui.cost_table(UsageTracker()))


def test_rates_text_never_invents_a_price(tmp_path: Path) -> None:
    book = PriceBook(tmp_path)
    assert "$3.3 in, $16.5 out" in cli._rates_text(book, "in.moonshotai.kimi-k3")
    assert "unknown" in cli._rates_text(book, "new.model")
    assert "$" not in cli._rates_text(book, "auto")


def test_sessions_table_shows_saved_cost() -> None:
    from code_harness.sessions import SessionInfo

    infos = [
        SessionInfo("a" * 32, "t", 1000.0, 1, {"spent": 1.5}),
        SessionInfo("b" * 32, "t", 1000.0, 1, {"spent": "x"}),
        SessionInfo("c" * 32, "t", 1000.0, 1),
    ]
    text = _render(ui.sessions_table(infos, "", 1000.0))
    assert "~$1.50" in text and text.count("$") == 1


def test_tool_rows_and_help() -> None:
    assert "Real machine git push" in _render(ui.tool_row("run_on_host git push", "utf-8"))
    assert "mcp_thing x" in _render(ui.tool_row("mcp_thing x", "utf-8"))
    assert "/cost" in _render(ui.help_table())
