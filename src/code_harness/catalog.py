"""Bedrock model catalog with data-residency tiers, context ranking and an `auto` choice.

Tiers, in priority order:

1. ``india``  - ``in.*`` inference profiles: requests route only within India's AWS regions.
2. ``mumbai`` - on-demand models hosted in ``ap-south-1`` (Mumbai), called by plain model id.

Discovery uses read-only Bedrock control-plane listings (no inference, no charge). Context windows
come from the Strands SDK's model table when it knows the model ("sdk"); otherwise from the
provider's published context length ("estimate"; Bedrock may serve less). A model is eligible for
``auto`` only after a recorded tool-use probe succeeded, because the agent needs tool calling.
"""

import json
import os
import re
import tempfile
import time
import warnings
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

HOME_REGION = "ap-south-1"
TIERS = ("india", "mumbai")
TIER_LABELS = {
    "india": "India inference (in.*; stays in India's AWS regions)",
    "mumbai": "Mumbai in-region (ap-south-1 on-demand)",
}

# From the AWS Bedrock model cards (docs.aws.amazon.com/bedrock/latest/userguide/model-card-*).
CONTEXT_AWS_DOCS: dict[str, int] = {
    "in.moonshotai.kimi-k3": 1_000_000,
    "moonshotai.kimi-k3": 1_000_000,
    "us.moonshotai.kimi-k3": 1_000_000,
    "global.moonshotai.kimi-k3": 1_000_000,
}

# Provider-published context lengths for models the SDK table does not cover. Estimates only.
CONTEXT_ESTIMATES: dict[str, int] = {
    "qwen.qwen3-coder-480b-a35b-v1:0": 262_144,
    "qwen.qwen3-coder-30b-a3b-v1:0": 262_144,
    "qwen.qwen3-235b-a22b-2507-v1:0": 262_144,
    "qwen.qwen3-next-80b-a3b": 262_144,
    "qwen.qwen3-vl-235b-a22b": 262_144,
    "qwen.qwen3-32b-v1:0": 32_768,
    "moonshotai.kimi-k2.5": 262_144,
    "moonshot.kimi-k2-thinking": 262_144,
    "mistral.devstral-2-123b": 256_000,
    "minimax.minimax-m2": 204_800,
    "minimax.minimax-m2.1": 204_800,
    "minimax.minimax-m2.5": 204_800,
    "deepseek.v3.2": 128_000,
    "deepseek.v3-v1:0": 128_000,
    "nvidia.nemotron-nano-12b-v2": 128_000,
    "nvidia.nemotron-nano-9b-v2": 128_000,
    "google.gemma-3-27b-it": 128_000,
    "google.gemma-3-12b-it": 128_000,
    "google.gemma-3-4b-it": 128_000,
    "mistral.mixtral-8x7b-instruct-v0:1": 32_000,
    "mistral.mistral-7b-instruct-v0:2": 32_000,
    "mistral.voxtral-small-24b-2507": 32_000,
    "mistral.voxtral-mini-3b-2507": 32_000,
    "meta.llama3-70b-instruct-v1:0": 8_000,
    "meta.llama3-8b-instruct-v1:0": 8_000,
}
# Not useful as a coding agent (embeddings, guard models, audio-first, legacy).
EXCLUDED = re.compile(r"embed|safeguard|voxtral|claude-3-")


# Claude models and Kimi K3 (AWS model card: "Input: Text and Image").
VISION_MARKERS = ("anthropic.claude-", "moonshotai.kimi-k3")


@dataclass(frozen=True)
class CatalogModel:
    model_id: str
    tier: str
    provider: str
    context: int | None
    context_source: str  # "sdk", "aws-docs", "estimate" or "unknown"
    tools: str  # "ok", "failed" (no tool call), "no_access" or "untested"


def _provider(model_id: str) -> str:
    parts = model_id.split(".")
    return parts[1] if parts[0] in {"in", "apac", "global"} and len(parts) > 2 else parts[0]


def supports_images(model_id: str) -> bool:
    """Models documented to accept images; anything else is treated as text-only."""
    return any(marker in model_id for marker in VISION_MARKERS)


def sdk_context(model_id: str) -> int | None:
    """Context window from the Strands SDK's model table, if it knows the id."""
    from strands.models import BedrockModel

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        limit = BedrockModel(model_id=model_id, region_name=HOME_REGION).model_info.limits
    return limit.context_window or None


def context_for(model_id: str) -> tuple[int | None, str]:
    known = sdk_context(model_id)
    if known:
        return known, "sdk"
    if model_id in CONTEXT_AWS_DOCS:
        return CONTEXT_AWS_DOCS[model_id], "aws-docs"
    if model_id in CONTEXT_ESTIMATES:
        return CONTEXT_ESTIMATES[model_id], "estimate"
    return None, "unknown"


def discover(region: str = HOME_REGION, client_factory: Callable[..., Any] | None = None) -> dict:
    """Read-only listing of India-routed profiles and Mumbai on-demand text models."""
    if client_factory is None:
        import boto3  # type: ignore[import-untyped]

        client_factory = boto3.client
    bedrock = client_factory("bedrock", region_name=region)
    india: list[str] = []
    token: str | None = None
    while True:
        page = bedrock.list_inference_profiles(**({"nextToken": token} if token else {}))
        india += [
            item["inferenceProfileId"]
            for item in page.get("inferenceProfileSummaries", [])
            if item.get("inferenceProfileId", "").startswith("in.")
            and item.get("status") == "ACTIVE"
        ]
        token = page.get("nextToken")
        if not token:
            break
    models = bedrock.list_foundation_models(byInferenceType="ON_DEMAND", byOutputModality="TEXT")
    mumbai = [
        item["modelId"]
        for item in models.get("modelSummaries", [])
        if item.get("modelLifecycle", {}).get("status", "ACTIVE") == "ACTIVE"
        and "TEXT" in item.get("inputModalities", ["TEXT"])
    ]
    return {"india": sorted(set(india)), "mumbai": sorted(set(mumbai)), "time": time.time()}


def build(listing: dict, probes: dict[str, str]) -> list[CatalogModel]:
    """Rank: tier priority, then tool status, then largest context."""
    models: list[CatalogModel] = []
    for tier in TIERS:
        for model_id in listing.get(tier, []):
            if EXCLUDED.search(model_id):
                continue
            context, source = context_for(model_id)
            models.append(
                CatalogModel(
                    model_id,
                    tier,
                    _provider(model_id),
                    context,
                    source,
                    probes.get(model_id, "untested"),
                )
            )
    tool_rank = {"ok": 0, "untested": 1, "failed": 2, "no_access": 3}
    models.sort(
        key=lambda m: (TIERS.index(m.tier), tool_rank[m.tools], -(m.context or 0), m.model_id)
    )
    return models


def choose_auto(
    models: Iterable[CatalogModel], tiers: Iterable[str] = TIERS
) -> CatalogModel | None:
    """Highest-context tool-capable model in the first allowed tier that has one."""
    allowed = list(tiers)
    pool = [m for m in models if m.tools == "ok" and m.tier in allowed]
    pool.sort(key=lambda m: (allowed.index(m.tier), -(m.context or 0), m.model_id))
    return pool[0] if pool else None


# ---- on-disk cache (listing + probe results) ----------------------------------------------


def cache_path(state_dir: Path) -> Path:
    return state_dir / "model-catalog.json"


def load_cache(state_dir: Path) -> dict:
    try:
        data = json.loads(cache_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    probes = data.get("probes", {})
    listing = data.get("listing", {})
    valid = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}")
    clean_probes = (
        {
            k: v
            for k, v in probes.items()
            if isinstance(k, str) and valid.fullmatch(k) and v in {"ok", "failed", "no_access"}
        }
        if isinstance(probes, dict)
        else {}
    )
    clean_listing: dict[str, Any] = {}
    if isinstance(listing, dict):
        for tier in TIERS:
            ids = listing.get(tier, [])
            if isinstance(ids, list):
                clean_listing[tier] = [i for i in ids if isinstance(i, str) and valid.fullmatch(i)]
        if isinstance(listing.get("time"), (int, float)):
            clean_listing["time"] = listing["time"]
    return {"listing": clean_listing, "probes": clean_probes}


def save_cache(state_dir: Path, data: dict) -> None:
    state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="catalog-", suffix=".json", dir=state_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
        os.replace(name, cache_path(state_dir))
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


# ---- tool-use probe (one small billed call per model) --------------------------------------


def probe_tool_use(model_id: str, region: str = HOME_REGION, client: Any = None) -> str:
    """'ok' if the model answers with a well-formed tool call; 'no_access' or 'failed' otherwise."""
    if client is None:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=region)
    tool = {
        "toolSpec": {
            "name": "get_word",
            "description": "Return the secret word.",
            "inputSchema": {"json": {"type": "object", "properties": {}, "required": []}},
        }
    }
    try:
        response = client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "Call the get_word tool now."}]}],
            toolConfig={"tools": [tool]},
            inferenceConfig={"maxTokens": 300},
        )
    except Exception as exc:  # noqa: BLE001 - classify, never surface provider text
        from code_harness.routing import classify

        return "no_access" if classify(exc) == "drop" else "failed"
    blocks = response.get("output", {}).get("message", {}).get("content", [])
    called = any(block.get("toolUse", {}).get("name") == "get_word" for block in blocks)
    return "ok" if called else "failed"


def as_rows(models: Iterable[CatalogModel]) -> list[dict]:
    return [asdict(model) for model in models]


LISTING_MAX_AGE = 24 * 3600
MAX_AUTO_PROBES = 6


def load_catalog(
    state_dir: Path,
    *,
    refresh: bool = False,
    discover_fn: Callable[[], dict] = discover,
) -> tuple[list[CatalogModel], dict]:
    """Catalog from cache, re-listing (read-only, free) when missing, stale or requested."""
    cache = load_cache(state_dir)
    listing = cache.get("listing", {})
    stale = time.time() - float(listing.get("time", 0)) > LISTING_MAX_AGE
    if refresh or stale or not any(listing.get(tier) for tier in TIERS):
        listing = discover_fn()
        cache["listing"] = listing
        save_cache(state_dir, cache)
    return build(listing, cache.get("probes", {})), cache


def record_probe(state_dir: Path, model_id: str, status: str | bool) -> None:
    cache = load_cache(state_dir)
    if isinstance(status, bool):
        status = "ok" if status else "failed"
    if status not in {"ok", "failed", "no_access"}:
        raise ValueError(f"unknown probe status {status!r}")
    cache.setdefault("probes", {})[model_id] = status
    save_cache(state_dir, cache)


def resolve_auto(
    state_dir: Path,
    tiers: Iterable[str] = TIERS,
    *,
    notify: Callable[[str], None] = print,
    probe_fn: Callable[[str], str | bool] = probe_tool_use,
    discover_fn: Callable[[], dict] = discover,
) -> CatalogModel | None:
    """Pick the best model; probe untested candidates (one small billed call each) if needed."""
    allowed = list(tiers)
    models, _ = load_catalog(state_dir, discover_fn=discover_fn)
    best = choose_auto(models, allowed)
    untested = sorted(
        (m for m in models if m.tools == "untested" and m.tier in allowed and m.context),
        key=lambda m: (allowed.index(m.tier), -(m.context or 0), m.model_id),
    )
    # Probe only candidates that could beat the current best (earlier tier or larger window).
    for candidate in untested[:MAX_AUTO_PROBES]:
        if best is not None and (
            allowed.index(candidate.tier),
            -(candidate.context or 0),
        ) >= (allowed.index(best.tier), -(best.context or 0)):
            break
        notify(f"Checking tool support of {candidate.model_id} (one small model call)...")
        status = probe_fn(candidate.model_id)
        record_probe(state_dir, candidate.model_id, status)
        if status in {True, "ok"}:
            models, _ = load_catalog(state_dir, discover_fn=discover_fn)
            best = choose_auto(models, allowed)
            break
    return best


MAX_ROUTE_CANDIDATES = 4


def routing_candidates(state_dir: Path, tiers: Iterable[str] = TIERS) -> list[CatalogModel]:
    """Probed tool-capable models in the allowed tiers, best first, for the auto router."""
    allowed = list(tiers)
    models = build(
        load_cache(state_dir).get("listing", {}), load_cache(state_dir).get("probes", {})
    )
    pool = [m for m in models if m.tools == "ok" and m.tier in allowed]
    pool.sort(key=lambda m: (allowed.index(m.tier), -(m.context or 0), m.model_id))
    return pool[:MAX_ROUTE_CANDIDATES]
