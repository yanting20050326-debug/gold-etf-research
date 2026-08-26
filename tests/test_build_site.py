import json
from datetime import UTC, datetime

from gold_research.build_site import build_payload, render_html
from gold_research.fetch_macro import MacroSeries, PricePoint
from gold_research.fetch_twse import DailyBar


def _sample_bars() -> list[DailyBar]:
    closes = [
        46.0,
        46.2,
        46.5,
        46.3,
        46.8,
        47.0,
        46.9,
        47.2,
        47.5,
        47.1,
        47.3,
        47.6,
        47.8,
        47.4,
        47.9,
        48.0,
        48.2,
        48.1,
        48.4,
        48.6,
        48.3,
        48.7,
        48.9,
        49.0,
        48.8,
        49.2,
        49.4,
        49.1,
        49.5,
        49.7,
    ]
    bars = []
    for i, close in enumerate(closes):
        day = i + 1 if i < 20 else i - 19
        month = 8 if i < 20 else 9
        bars.append(
            DailyBar(
                trading_date=f"2026-{month:02d}-{day:02d}",
                open=close - 0.1,
                high=close + 0.2,
                low=close - 0.2,
                close=close,
                volume=1_000_000 + i,
            )
        )
    return bars


def test_build_payload_has_expected_schema():
    bars = _sample_bars()
    macro_gold = MacroSeries(
        symbol="GC=F",
        points=[PricePoint("2026-08-20", 2050.0)],
        as_of="2026-08-20",
        stale=False,
    )
    macro_fx = MacroSeries(
        symbol="USDTWD=X",
        points=[PricePoint("2026-08-20", 31.5)],
        as_of="2026-08-20",
        stale=False,
    )
    payload = build_payload(
        bars, macro_gold, macro_fx, datetime(2026, 8, 20, tzinfo=UTC)
    )

    assert payload["disclaimer"]
    target = payload["targets"]["00635U"]
    assert target["asset_class"] == "commodity_futures_etf"
    assert target["indicators"]["ma20"] is not None
    assert target["indicators"]["rsi14"] is not None
    assert len(target["chart"]) == len(bars)
    assert payload["macro_context"]["comex_gold_usd"][0]["close"] == 2050.0
    assert len(payload["candidates"]) == 5


def test_build_payload_flags_stale_macro_context():
    bars = _sample_bars()
    macro_gold = MacroSeries(symbol="GC=F", points=[], as_of="2026-08-19", stale=True)
    macro_fx = MacroSeries(
        symbol="USDTWD=X", points=[], as_of="2026-08-19", stale=False
    )
    payload = build_payload(
        bars, macro_gold, macro_fx, datetime(2026, 8, 20, tzinfo=UTC)
    )
    assert payload["macro_context"]["stale"] is True


def test_render_html_embeds_valid_json():
    bars = _sample_bars()
    macro_gold = MacroSeries(symbol="GC=F", points=[], as_of="2026-08-20", stale=True)
    macro_fx = MacroSeries(symbol="USDTWD=X", points=[], as_of="2026-08-20", stale=True)
    payload = build_payload(
        bars, macro_gold, macro_fx, datetime(2026, 8, 20, tzinfo=UTC)
    )
    html = render_html(payload)

    assert "const SITE_DATA = " in html
    start = html.index("const SITE_DATA = ") + len("const SITE_DATA = ")
    end = html.index(";\n", start)
    embedded = json.loads(html[start:end])
    assert embedded["targets"]["00635U"]["code"] == "00635U"


def test_render_html_includes_price_charts():
    bars = _sample_bars()
    macro_gold = MacroSeries(
        symbol="GC=F",
        points=[PricePoint("2026-08-20", 2050.0)],
        as_of="2026-08-20",
        stale=False,
    )
    macro_fx = MacroSeries(
        symbol="USDTWD=X",
        points=[PricePoint("2026-08-20", 31.5)],
        as_of="2026-08-20",
        stale=False,
    )
    payload = build_payload(
        bars, macro_gold, macro_fx, datetime(2026, 8, 20, tzinfo=UTC)
    )
    html = render_html(payload)

    # These assert the specific call sites exist, not just that the string
    # "renderSparkline" appears somewhere in the template — a template
    # constant containing the word would make this test pass even if the
    # actual rendering calls were deleted. Asserting the exact call-site
    # source text is immune to that.
    assert "renderSparkline(target.chart" in html
    assert "renderSparkline(goldPoints" in html
    assert "renderSparkline(fxPoints" in html


def test_build_payload_includes_new_indicators_and_divergence():
    bars = _sample_bars()
    dates = [f"2026-08-{d:02d}" for d in range(15, 21)]
    macro_gold = MacroSeries(
        symbol="GC=F",
        points=[PricePoint(d, 2000.0 + i) for i, d in enumerate(dates)],
        as_of=dates[-1],
        stale=False,
    )
    macro_fx = MacroSeries(
        symbol="USDTWD=X",
        points=[PricePoint(d, 31.0 + i * 0.1) for i, d in enumerate(dates)],
        as_of=dates[-1],
        stale=False,
    )
    payload = build_payload(
        bars, macro_gold, macro_fx, datetime(2026, 8, 20, tzinfo=UTC)
    )

    target_indicators = payload["targets"]["00635U"]["indicators"]
    assert target_indicators["volatility_squeeze"]["ratio"] is not None
    assert target_indicators["relative_position"]["label"] in (
        "相對低點區",
        "區間中段",
        "相對高點區",
    )
    divergence = payload["macro_context"]["divergence"]
    assert divergence["checked_days"] == 5
    assert divergence["same_direction_days"] == 5  # both series rise every day here
    assert divergence["is_divergent"] is True


def test_render_html_includes_new_indicator_cards_and_divergence():
    bars = _sample_bars()
    macro_gold = MacroSeries(
        symbol="GC=F",
        points=[PricePoint("2026-08-20", 2050.0)],
        as_of="2026-08-20",
        stale=False,
    )
    macro_fx = MacroSeries(
        symbol="USDTWD=X",
        points=[PricePoint("2026-08-20", 31.5)],
        as_of="2026-08-20",
        stale=False,
    )
    payload = build_payload(
        bars, macro_gold, macro_fx, datetime(2026, 8, 20, tzinfo=UTC)
    )
    html = render_html(payload)

    assert 'indicatorCard("volatility_squeeze"' in html
    assert 'indicatorCard("relative_position"' in html
    assert "macro.divergence" in html
