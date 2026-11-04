import json
from pathlib import Path

import pytest

from code_harness.price_snapshot import PRICES
from code_harness.pricing import (
    PRICE_NAMES,
    Price,
    PriceBook,
    format_usd,
    parse_price_rows,
    refresh_prices,
)


def row(model: str, usagetype: str, kind: str, usd: str, unit: str = "1K tokens") -> dict:
    return {"model": model, "usagetype": usagetype, "kind": kind, "usd": usd, "unit": unit}


def test_only_standard_on_demand_rates_are_used() -> None:
    rows = [
        row("Kimi K2.5", "APS3-kimi-input-tokens", "Input tokens", "0.00072"),
        row("Kimi K2.5", "APS3-kimi-output-tokens", "Output tokens", "0.0036"),
        row("Kimi K2.5", "APS3-kimi-input-tokens-batch", "input tokens batch", "0.1"),
        row("Kimi K2.5", "APS3-kimi-input-tokens-flex", "Input tokens flex", "0.2"),
        row("Kimi K2.5", "APS3-kimi-output-tokens-priority", "Output tokens priority", "0.3"),
        row("Kimi K2.5", "APS3-kimi-output-reserved", "Output tokens", "0.4", "1M tokens"),
    ]
    assert parse_price_rows(rows) == {"moonshotai.kimi-k2.5": {"input": 0.72, "output": 3.6}}


def test_mantle_rates_are_only_a_fallback() -> None:
    rows = [
        row("Kimi K3", "APS3-k3-mantle-input-tokens-standard", "Input tokens", "0.0033"),
        row("Kimi K3", "APS3-k3-mantle-output-tokens-standard", "Output tokens", "0.0165"),
        row("Kimi K3", "APS3-k3-mantle-cache-read-tokens-standard", "Cache read tokens", "0.00033"),
        row("Kimi K3", "APS3-k3-mantle-input-tokens-global-standard", "Input tokens global", "9"),
        row("Kimi K3", "APS3-k3-mantle-cache-write-tokens-30m", "Cache write tokens 30m", "9"),
        row("Kimi K2.5", "APS3-k-input-tokens", "Input tokens", "0.001"),
        row("Kimi K2.5", "APS3-k-output-tokens", "Output tokens", "0.002"),
        row("Kimi K2.5", "APS3-k-mantle-input-tokens-standard", "Input tokens", "0.9"),
        row("Kimi K2.5", "APS3-k-mantle-output-tokens-standard", "Output tokens", "0.9"),
    ]
    prices = parse_price_rows(rows)
    assert prices["in.moonshotai.kimi-k3"] == {"input": 3.3, "output": 16.5, "cached": 0.33}
    assert prices["moonshotai.kimi-k2.5"] == {"input": 1.0, "output": 2.0}


def test_claude_cache_rates_and_million_units() -> None:
    rows = [
        row(
            "Claude Haiku 4.5", "MP:APS3_InputTokenCount-Units", "Input tokens", "1.1", "1M tokens"
        ),
        row(
            "Claude Haiku 4.5",
            "MP:APS3_InputTokenCount_Global-Units",
            "Input tokens",
            "1.0",
            "1M tokens",
        ),
        row(
            "Claude Haiku 4.5",
            "MP:APS3_OutputTokenCount-Units",
            "Output tokens",
            "5.5",
            "1M tokens",
        ),
        row(
            "Claude Haiku 4.5",
            "MP:APS3_CacheReadInputTokenCount-Units",
            "Prompt cache read input tokens",
            "0.11",
            "1M tokens",
        ),
        row(
            "Claude Haiku 4.5",
            "MP:APS3_CacheWriteInputTokenCount-Units",
            "Prompt cache write input tokens",
            "1.375",
            "1M tokens",
        ),
    ]
    assert parse_price_rows(rows)["in.anthropic.claude-haiku-4-5-20251001-v1:0"] == {
        "input": 1.1,
        "output": 5.5,
        "cached": 0.11,
    }


def test_model_without_both_rates_or_a_known_name_stays_unpriced() -> None:
    rows = [
        row("Kimi K2.5", "APS3-k-input-tokens", "Input tokens", "0.001"),
        row("Some New Model", "APS3-n-input-tokens", "Input tokens", "0.001"),
        row("Some New Model", "APS3-n-output-tokens", "Output tokens", "0.001"),
        row("GLM 5", "APS3-g-input-tokens", "Input tokens", "bad"),
        row("GLM 5", "APS3-g-output-tokens", "Output tokens", "0.001", "per hour"),
    ]
    assert parse_price_rows(rows) == {}


def test_snapshot_matches_the_console_price_and_covers_every_mapped_model() -> None:
    assert PRICES["in.moonshotai.kimi-k3"] == {"input": 3.3, "output": 16.5, "cached": 0.33}
    assert set(PRICES) <= set(PRICE_NAMES)
    assert all(rates["input"] > 0 and rates["output"] > 0 for rates in PRICES.values())


def test_cost_arithmetic() -> None:
    price = Price(3.3, 16.5, 0.33, "x")
    assert price.cost(1_000_000, 0, 0) == pytest.approx(3.3)
    assert price.cost(0, 1_000_000, 0) == pytest.approx(16.5)
    assert price.cost(0, 0, 1_000_000) == pytest.approx(0.33)
    # No listed cached rate: charged at the input rate, an upper bound.
    assert Price(1.0, 2.0, None, "x").cost(0, 0, 1_000_000) == pytest.approx(1.0)
    assert format_usd(0.00412) == "$0.0041" and format_usd(1234.5) == "$1,234.50"


def test_lookup_order(tmp_path: Path) -> None:
    book = PriceBook(tmp_path)
    kimi = book.get("in.moonshotai.kimi-k3")
    assert kimi is not None and kimi.input == 3.3 and "snapshot" in kimi.source
    assert book.get("unknown.model") is None
    assert book.get("") is None

    (tmp_path / "prices.json").write_text(
        json.dumps(
            {"taken": "2027-01-01", "prices": {"in.moonshotai.kimi-k3": {"input": 9, "output": 8}}}
        )
    )
    refreshed = PriceBook(tmp_path).get("in.moonshotai.kimi-k3")
    assert refreshed is not None and refreshed.input == 9 and "2027-01-01" in refreshed.source

    mine = PriceBook(tmp_path, input_override=1, output_override=2, cached_override=0.5)
    assert mine.get("anything") == Price(1, 2, 0.5, "your settings")
    # One override alone cannot price a model.
    half = PriceBook(tmp_path, input_override=1)
    assert half.get("unknown.model") is None


def test_other_regions_stay_unknown_unless_the_user_sets_a_price(tmp_path: Path) -> None:
    assert PriceBook(tmp_path, region="us-east-1").get("in.moonshotai.kimi-k3") is None
    book = PriceBook(tmp_path, region="us-east-1", input_override=1, output_override=2)
    assert book.get("in.moonshotai.kimi-k3") is not None


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"prices": {}}',
        '{"taken": "x", "prices": []}',
        '{"taken": "x", "prices": {"in.moonshotai.kimi-k3": {"input": -1, "output": 1}}}',
        '{"taken": "x", "prices": {"in.moonshotai.kimi-k3": {"input": true, "output": 1}}}',
    ],
)
def test_damaged_price_file_never_gives_a_wrong_rate(tmp_path: Path, content: str) -> None:
    (tmp_path / "prices.json").write_text(content)
    price = PriceBook(tmp_path).get("in.moonshotai.kimi-k3")
    # Either the snapshot is used or the model is unknown; a bad rate is never trusted.
    assert price is None or price.input == 3.3


class FakePricing:
    def __init__(self, pages: list[list[dict]]) -> None:
        self.pages = pages

    def get_products(self, **request):
        index = int(request.get("NextToken", 0))
        if request["ServiceCode"] != "AmazonBedrock":
            return {"PriceList": []}
        items = [json.dumps(item) for item in self.pages[index]]
        more = index + 1 < len(self.pages)
        return {"PriceList": items, **({"NextToken": str(index + 1)} if more else {})}


def _product(model: str, usage: str, kind: str, usd: str) -> dict:
    return {
        "product": {"attributes": {"model": model, "usagetype": usage, "inferenceType": kind}},
        "terms": {
            "OnDemand": {
                "t": {"priceDimensions": {"d": {"pricePerUnit": {"USD": usd}, "unit": "1K tokens"}}}
            }
        },
    }


def test_refresh_reads_every_page_and_saves(tmp_path: Path) -> None:
    client = FakePricing(
        [
            [_product("GLM 5", "APS3-g-input-tokens", "Input tokens", "0.002")],
            [_product("GLM 5", "APS3-g-output-tokens", "Output tokens", "0.003")],
        ]
    )
    assert refresh_prices(tmp_path, client) == 1
    price = PriceBook(tmp_path).get("zai.glm-5")
    assert price is not None and (price.input, price.output) == (2.0, 3.0)
    assert "AWS price list" in price.source


def test_refresh_failure_keeps_the_old_file(tmp_path: Path) -> None:
    (tmp_path / "prices.json").write_text(
        '{"taken": "x", "prices": {"a": {"input": 1, "output": 1}}}'
    )
    with pytest.raises(ValueError, match="no usable"):
        refresh_prices(tmp_path, FakePricing([[]]))
    assert "taken" in (tmp_path / "prices.json").read_text()
