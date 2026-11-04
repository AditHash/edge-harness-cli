import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_harness import catalog
from code_harness.catalog import CatalogModel, build, choose_auto, resolve_auto

LISTING = {
    "india": [
        "in.anthropic.claude-haiku-4-5-20251001-v1:0",
        "in.anthropic.claude-sonnet-5",
        "in.moonshotai.kimi-k3",
    ],
    "mumbai": [
        "qwen.qwen3-coder-480b-a35b-v1:0",
        "mistral.devstral-2-123b",
        "openai.gpt-oss-120b-1:0",
        "amazon.titan-embed-text-v2:0",
        "openai.gpt-oss-safeguard-120b",
        "meta.llama3-8b-instruct-v1:0",
    ],
    "time": 9e12,
}


def test_build_ranks_tier_then_tools_then_context() -> None:
    probes = {"in.anthropic.claude-haiku-4-5-20251001-v1:0": "ok"}
    models = build(LISTING, probes)
    ids = [m.model_id for m in models]
    assert "amazon.titan-embed-text-v2:0" not in ids
    assert "openai.gpt-oss-safeguard-120b" not in ids
    india = [m for m in models if m.tier == "india"]
    assert india[0].model_id.startswith("in.anthropic.claude-haiku")  # probed ok first
    assert all(m.tier == "india" for m in models[: len(india)])
    sonnet = next(m for m in models if m.model_id == "in.anthropic.claude-sonnet-5")
    assert (sonnet.context, sonnet.context_source) == (1_000_000, "sdk")
    qwen = next(m for m in models if m.model_id.startswith("qwen.qwen3-coder-480b"))
    assert (qwen.context, qwen.context_source) == (262_144, "estimate")
    kimi = next(m for m in models if m.model_id == "in.moonshotai.kimi-k3")
    assert kimi.context_source == "unknown"
    mumbai = [m.model_id for m in models if m.tier == "mumbai"]
    assert mumbai.index("qwen.qwen3-coder-480b-a35b-v1:0") < mumbai.index(
        "meta.llama3-8b-instruct-v1:0"
    )


def test_auto_prefers_india_then_largest_context_and_needs_tools() -> None:
    models = [
        CatalogModel("in.a", "india", "a", 200_000, "sdk", "ok"),
        CatalogModel("in.b", "india", "b", 1_000_000, "sdk", "failed"),
        CatalogModel("m.c", "mumbai", "c", 2_000_000, "estimate", "ok"),
        CatalogModel("in.d", "india", "d", 900_000, "sdk", "ok"),
    ]
    assert choose_auto(models).model_id == "in.d"
    assert choose_auto(models, ["mumbai"]).model_id == "m.c"
    assert choose_auto(models, ["mumbai", "india"]).model_id == "m.c"
    assert choose_auto([m for m in models if m.tools != "ok"]) is None


def test_resolve_auto_probes_best_candidates_and_caches(tmp_path: Path) -> None:
    probed: list[str] = []

    def probe(model_id: str) -> bool:
        probed.append(model_id)
        return model_id == "in.anthropic.claude-sonnet-5"

    chosen = resolve_auto(
        tmp_path, notify=lambda text: None, probe_fn=probe, discover_fn=lambda: dict(LISTING)
    )
    assert chosen is not None and chosen.model_id == "in.anthropic.claude-sonnet-5"
    assert probed == ["in.anthropic.claude-sonnet-5"]  # largest India window first
    saved = json.loads(catalog.cache_path(tmp_path).read_text())
    assert saved["probes"] == {"in.anthropic.claude-sonnet-5": "ok"}

    probed.clear()
    again = resolve_auto(
        tmp_path,
        notify=lambda text: None,
        probe_fn=probe,
        discover_fn=lambda: pytest.fail("relisted"),
    )
    assert again is not None and again.model_id == "in.anthropic.claude-sonnet-5" and probed == []


def test_resolve_auto_falls_back_to_mumbai_when_india_fails(tmp_path: Path) -> None:
    def probe(model_id: str) -> bool:
        return not model_id.startswith("in.")

    chosen = resolve_auto(
        tmp_path, notify=lambda text: None, probe_fn=probe, discover_fn=lambda: dict(LISTING)
    )
    assert chosen is not None and chosen.model_id == "qwen.qwen3-coder-480b-a35b-v1:0"


def test_tampered_cache_is_sanitized(tmp_path: Path) -> None:
    catalog.cache_path(tmp_path).write_text(
        json.dumps(
            {
                "probes": {"in.x": "ok", "bad id; rm": "ok", "in.y": "maybe"},
                "listing": {"india": ["in.x", 5, "../../etc"], "time": "x"},
            }
        )
    )
    cache = catalog.load_cache(tmp_path)
    assert cache["probes"] == {"in.x": "ok"}
    assert cache["listing"] == {"india": ["in.x"], "mumbai": []}


def test_probe_detects_tool_call_and_fails_closed() -> None:
    class Client:
        def __init__(self, response=None, error=None):
            self.response, self.error = response, error

        def converse(self, **kwargs):
            assert kwargs["inferenceConfig"]["maxTokens"] <= 300
            if self.error:
                raise self.error
            return self.response

    tool_reply = {"output": {"message": {"content": [{"toolUse": {"name": "get_word"}}]}}}
    text_reply = {"output": {"message": {"content": [{"text": "the word is x"}]}}}
    assert catalog.probe_tool_use("m", client=Client(tool_reply)) == "ok"
    assert catalog.probe_tool_use("m", client=Client(text_reply)) == "failed"
    assert catalog.probe_tool_use("m", client=Client(error=RuntimeError("boom"))) == "failed"

    class Denied(Exception):
        response = {"Error": {"Code": "AccessDeniedException"}}  # noqa: RUF012

    assert catalog.probe_tool_use("m", client=Client(error=Denied())) == "no_access"


def test_discover_pages_and_filters() -> None:
    class Bedrock:
        def list_inference_profiles(self, **kwargs):
            if "nextToken" not in kwargs:
                return {
                    "inferenceProfileSummaries": [
                        {"inferenceProfileId": "in.a", "status": "ACTIVE"},
                        {"inferenceProfileId": "apac.b", "status": "ACTIVE"},
                    ],
                    "nextToken": "t",
                }
            return {
                "inferenceProfileSummaries": [{"inferenceProfileId": "in.c", "status": "ACTIVE"}]
            }

        def list_foundation_models(self, **kwargs):
            assert kwargs == {"byInferenceType": "ON_DEMAND", "byOutputModality": "TEXT"}
            return {
                "modelSummaries": [
                    {"modelId": "qwen.x", "modelLifecycle": {"status": "ACTIVE"}},
                    {"modelId": "old.y", "modelLifecycle": {"status": "LEGACY"}},
                ]
            }

    listing = catalog.discover(client_factory=lambda *a, **k: Bedrock())
    assert listing["india"] == ["in.a", "in.c"] and listing["mumbai"] == ["qwen.x"]


def test_unknown_to_sdk_model_gets_estimated_context_for_auto_compaction(tmp_path: Path) -> None:
    from code_harness.agent import create_agent
    from code_harness.config import Settings

    settings = Settings(
        workspace=tmp_path,
        bedrock_model_id="qwen.qwen3-coder-480b-a35b-v1:0",
        aws_region="ap-south-1",
    )
    agent = create_agent(settings)
    assert agent.model.context_window_limit == 262_144
    assert agent.conversation_manager._compression_threshold == 0.7
    known = create_agent(
        settings.model_copy(update={"bedrock_model_id": "in.anthropic.claude-sonnet-5"})
    )
    assert known.model.context_window_limit == 1_000_000


def test_stream_reports_auto_compaction() -> None:
    from code_harness.streaming import stream_turn

    class Agent:
        conversation_manager = SimpleNamespace(removed_message_count=4)

        async def stream_async(self, prompt, **kwargs):
            self.conversation_manager.removed_message_count = 10
            yield {"result": SimpleNamespace(stop_reason="end_turn", invocation_context=None)}

    async def collect():
        return [event async for event in stream_turn(Agent(), "hi")]

    events = asyncio.run(collect())
    assert events[0].kind == "compacted" and "6 older messages" in events[0].text


def test_context_report(tmp_path: Path) -> None:
    from code_harness.cli import _context_report
    from code_harness.config import Settings

    settings = Settings(workspace=tmp_path)
    agent = SimpleNamespace(
        model=SimpleNamespace(context_window_limit=200_000),
        event_loop_metrics=SimpleNamespace(latest_context_size=50_000),
    )
    report = _context_report(agent, settings)
    assert "50,000 / 200,000" in report and "25.0%" in report and "140,000" in report


def test_unknown_context_model_gets_cautious_limit_and_no_warning(tmp_path: Path) -> None:
    import warnings

    from code_harness.agent import UNKNOWN_CONTEXT_FALLBACK, create_agent
    from code_harness.config import Settings

    agent = create_agent(
        Settings(
            workspace=tmp_path, bedrock_model_id="in.moonshotai.kimi-k3", aws_region="ap-south-1"
        )
    )
    assert agent.model.context_window_limit == UNKNOWN_CONTEXT_FALLBACK
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert agent.model.estimate_utilization(64_000) == 0.5
