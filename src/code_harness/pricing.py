"""Model prices for cost estimates, from the AWS Price List API.

Lookup order: prices the user set, then `<state>/prices.json` from `--refresh-prices`, then the
snapshot shipped in `price_snapshot.py`. A model with no known price stays unknown; a price is never
guessed. Results are estimates, not a bill.

Only standard on-demand rates for ap-south-1 are read. Batch, flex, priority, reserved, global and
long-context rates are skipped, because this harness does not call models that way.
"""

import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from code_harness.price_snapshot import PRICES as SNAPSHOT
from code_harness.price_snapshot import TAKEN as SNAPSHOT_DATE

PRICE_REGION = "ap-south-1"
_SERVICES = ("AmazonBedrock", "AmazonBedrockFoundationModels")
_SKIP = (
    "batch",
    "flex",
    "priority",
    "reserved",
    "latency",
    "long_ctx",
    "global",
    "1h",
    "30m",
    "cache-write",
    "cache_write",
    "cachewrite",
)

# Catalog model id -> the model's name in the AWS price list. Explicit, because a loose match could
# price a model with its neighbour's rate. A model missing here has an unknown price.
PRICE_NAMES = {
    "in.moonshotai.kimi-k3": "Kimi K3",
    "in.anthropic.claude-haiku-4-5-20251001-v1:0": "Claude Haiku 4.5",
    "in.anthropic.claude-opus-5": "Claude Opus 5",
    "in.anthropic.claude-sonnet-5": "Claude Sonnet 5",
    "moonshot.kimi-k2-thinking": "Kimi K2 Thinking",
    "moonshotai.kimi-k2.5": "Kimi K2.5",
    "qwen.qwen3-235b-a22b-2507-v1:0": "Qwen3 235B A22B 2507",
    "qwen.qwen3-coder-30b-a3b-v1:0": "Qwen3 Coder 30B A3B",
    "qwen.qwen3-coder-480b-a35b-v1:0": "Qwen3 Coder 480B A35B",
    "qwen.qwen3-next-80b-a3b": "qwen3-next-80b-a3b",
    "qwen.qwen3-vl-235b-a22b": "Qwen3 VL 235B A22B",
    "qwen.qwen3-32b-v1:0": "Qwen3 32B",
    "mistral.devstral-2-123b": "Devstral",
    "mistral.mistral-large-3-675b-instruct": "Mistral Large 3",
    "mistral.magistral-small-2509": "Magistral Small 1.2",
    "mistral.ministral-3-14b-instruct": "Ministral 14B 3.0",
    "mistral.ministral-3-3b-instruct": "Ministral 3B 3.0",
    "mistral.ministral-3-8b-instruct": "Ministral 8B 3.0",
    "mistral.mistral-7b-instruct-v0:2": "Mistral 7B",
    "mistral.mistral-large-2402-v1:0": "Mistral Large",
    "mistral.mixtral-8x7b-instruct-v0:1": "Mixtral 8x7B",
    "minimax.minimax-m2": "Minimax M2",
    "minimax.minimax-m2.1": "Minimax M2.1",
    "minimax.minimax-m2.5": "MiniMax M2.5",
    "zai.glm-4.7": "GLM 4.7",
    "zai.glm-4.7-flash": "GLM 4.7 Flash",
    "zai.glm-5": "GLM 5",
    "deepseek.v3-v1:0": "DeepSeek V3.1",
    "deepseek.v3.2": "DeepSeek v3.2",
    "google.gemma-3-12b-it": "Gemma 3 12B",
    "google.gemma-3-27b-it": "Gemma 3 27B",
    "google.gemma-3-4b-it": "Gemma 3 4B",
    "nvidia.nemotron-nano-12b-v2": "NVIDIA Nemotron Nano 2 VL",
    "nvidia.nemotron-nano-9b-v2": "NVIDIA Nemotron Nano 2",
    "nvidia.nemotron-nano-3-30b": "Nemotron Nano 3 30B",
    "nvidia.nemotron-super-3-120b": "NVIDIA Nemotron 3 Super 120B A12B",
    "openai.gpt-oss-120b-1:0": "gpt-oss-120b",
    "openai.gpt-oss-20b-1:0": "gpt-oss-20b",
    "meta.llama3-70b-instruct-v1:0": "Llama 3 70B",
    "meta.llama3-8b-instruct-v1:0": "Llama 3 8B",
}


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens. Cached is None when AWS lists no cached rate."""

    input: float
    output: float
    cached: float | None
    source: str

    def cost(self, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
        # With no listed cached rate, charge the input rate: an upper bound, not a guess downward.
        cached_rate = self.input if self.cached is None else self.cached
        total = (
            input_tokens * self.input + output_tokens * self.output + cached_tokens * cached_rate
        )
        return total / 1_000_000


def format_usd(amount: float) -> str:
    return f"${amount:.4f}" if amount < 1 else f"${amount:,.2f}"


def _per_million(price: Any, unit: Any) -> float | None:
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    unit_text = str(unit).lower()
    if "1m tokens" in unit_text:
        return value
    if "1k tokens" in unit_text:
        return value * 1000
    return None


def parse_price_rows(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Reduce raw price rows to {model id: {input, output, cached}} per million tokens.

    A model must have both input and output rates to be priced. Rows for the regular endpoint win
    over `mantle` rows, which are used only for a model priced nowhere else.
    """
    names = {name: model_id for model_id, name in PRICE_NAMES.items()}
    found: dict[str, dict[str, dict[str, float]]] = {}
    for row in rows:
        model_id = names.get(str(row.get("model")))
        usage = str(row.get("usagetype", "")).lower()
        kind = str(row.get("kind", "")).lower()
        if model_id is None or any(word in usage or word in kind for word in _SKIP):
            continue
        per_million = _per_million(row.get("usd"), row.get("unit"))
        if per_million is None:
            continue
        if (
            "cache read" in kind
            or "cache-read" in usage
            or "cache_read" in usage
            or "cacheread" in usage
        ):
            field = "cached"
        elif "input" in kind:
            field = "input"
        elif "output" in kind:
            field = "output"
        else:
            continue
        endpoint = "mantle" if "mantle" in usage else "regular"
        found.setdefault(model_id, {}).setdefault(endpoint, {})[field] = round(per_million, 6)
    prices: dict[str, dict[str, float]] = {}
    for model_id, endpoints in found.items():
        for endpoint in ("regular", "mantle"):
            rates = endpoints.get(endpoint, {})
            if "input" in rates and "output" in rates:
                prices[model_id] = rates
                break
    return prices


def fetch_rows(client: Any = None) -> list[dict[str, Any]]:
    """Read every ap-south-1 Bedrock price row. A read-only call with no charge."""
    if client is None:
        import boto3  # type: ignore[import-untyped]

        client = boto3.client("pricing", region_name="us-east-1")
    rows: list[dict[str, Any]] = []
    for service in _SERVICES:
        token = None
        while True:
            request: dict[str, Any] = {
                "ServiceCode": service,
                "Filters": [
                    {"Type": "TERM_MATCH", "Field": "regionCode", "Value": PRICE_REGION},
                ],
                "MaxResults": 100,
            }
            if token:
                request["NextToken"] = token
            response = client.get_products(**request)
            for item in response["PriceList"]:
                product = json.loads(item)
                attributes = product["product"]["attributes"]
                for term in product["terms"].get("OnDemand", {}).values():
                    for dimension in term["priceDimensions"].values():
                        rows.append(
                            {
                                "model": attributes.get("model"),
                                "usagetype": attributes.get("usagetype"),
                                "kind": attributes.get("inferenceType"),
                                "usd": dimension["pricePerUnit"].get("USD"),
                                "unit": dimension["unit"],
                            }
                        )
            token = response.get("NextToken")
            if not token:
                break
    return rows


def _prices_path(state_dir: Path) -> Path:
    return state_dir / "prices.json"


def refresh_prices(state_dir: Path, client: Any = None) -> int:
    """Save current AWS prices; returns how many models were priced."""
    prices = parse_price_rows(fetch_rows(client))
    if not prices:
        raise ValueError("AWS returned no usable prices")
    state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="prices-", suffix=".json", dir=state_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"taken": date.today().isoformat(), "prices": prices}, stream, indent=1)
        os.replace(name, _prices_path(state_dir))
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return len(prices)


def _load_refreshed(state_dir: Path) -> tuple[dict[str, Any], str]:
    try:
        data = json.loads(_prices_path(state_dir).read_text(encoding="utf-8"))
        prices, taken = data["prices"], str(data["taken"])
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        return {}, ""
    return (prices, taken) if isinstance(prices, dict) else ({}, "")


def _number(value: Any) -> float | None:
    # bool is an int in Python; a saved `true` must not become a rate of 1.
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    return float(value)


@dataclass
class PriceBook:
    """Finds the rate for a model. Prices the user set apply to every model."""

    state_dir: Path
    region: str | None = PRICE_REGION
    input_override: float | None = None
    output_override: float | None = None
    cached_override: float | None = None

    def __post_init__(self) -> None:
        self.refreshed, self.refreshed_on = _load_refreshed(self.state_dir)

    def get(self, model_id: str) -> Price | None:
        if self.input_override is not None and self.output_override is not None:
            return Price(
                self.input_override, self.output_override, self.cached_override, "your settings"
            )
        if self.region != PRICE_REGION:
            return None
        rates = self.refreshed.get(model_id)
        source = f"AWS price list, {self.refreshed_on}"
        if not isinstance(rates, dict):
            rates, source = SNAPSHOT.get(model_id), f"built-in AWS snapshot, {SNAPSHOT_DATE}"
        if not rates:
            return None
        input_rate, output_rate = _number(rates.get("input")), _number(rates.get("output"))
        if input_rate is None or output_rate is None:
            return None
        return Price(input_rate, output_rate, _number(rates.get("cached")), source)
