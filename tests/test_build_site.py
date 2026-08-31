import json
from datetime import UTC, datetime

import pytest

from gold_research import build_site as build_site_module
from gold_research.build_site import build_payload, render_html
from gold_research.fetch_macro import LatestQuote, MacroSeries, PricePoint
from gold_research.fetch_twse import DailyBar, RealtimeQuote, TwseFetchError


def _sample_bars(base: float = 46.0) -> list[DailyBar]:
    offsets = [
        0.0,
        0.2,
        0.5,
        0.3,
        0.8,
        1.0,
        0.9,
        1.2,
        1.5,
        1.1,
        1.3,
        1.6,
        1.8,
        1.4,
        1.9,
        2.0,
        2.2,
        2.1,
        2.4,
        2.6,
        2.3,
        2.7,
        2.9,
        3.0,
        2.8,
        3.2,
        3.4,
        3.1,
        3.5,
        3.7,
    ]
    closes = [base + offset for offset in offsets]
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


def _sample_macro() -> tuple[MacroSeries, MacroSeries]:
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
    return macro_gold, macro_fx


def test_build_payload_has_expected_schema():
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )

    assert payload["disclaimer"]
    assert payload["auto_refresh_seconds"] == 60
    target = payload["targets"]["00635U"]
    assert target["asset_class"] == "commodity_futures_etf"
    assert target["indicators"]["ma20"] is not None
    assert target["indicators"]["rsi14"] is not None
    assert len(target["chart"]) == 30
    assert "candidates" not in payload


def test_build_payload_includes_multiple_twse_targets_and_intl_gold():
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars(46.0), "00708L": _sample_bars(20.0)},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )

    assert set(payload["targets"].keys()) == {"00635U", "00708L", "XAUUSD"}
    assert payload["targets"]["00708L"]["asset_class"] == "leveraged_futures_etf"
    intl_gold = payload["targets"]["XAUUSD"]
    assert intl_gold["asset_class"] == "commodity_spot"
    # 6 macro fixture points isn't enough for the 30-day relative_position
    # window; it's expected to be None, not the schema key being missing.
    assert "relative_position" in intl_gold["indicators"]
    assert intl_gold["latest"]["close"] == macro_gold.points[-1].close
    # Same 6-point shortage applies to the passbook estimate's own indicators,
    # but a latest converted price should still be available.
    passbook = intl_gold["passbook"]
    assert passbook["relative_position"] is None
    assert passbook["pullback_stage"] is None
    expected_latest = (
        macro_gold.points[-1].close * macro_fx.points[-1].close / 31.1034768
    )
    assert passbook["latest_price"] == pytest.approx(expected_latest)
    assert passbook["note"]


def test_intl_gold_passbook_estimate_computed_from_ntd_converted_series():
    dates = [f"2026-08-{d:02d}" for d in range(1, 32)] + [
        f"2026-09-{d:02d}" for d in range(1, 6)
    ]
    gold_closes = [2000.0 + i for i in range(len(dates))]  # steadily rising
    fx_closes = [31.0] * len(dates)  # flat FX, so USD trend passes through
    macro_gold = MacroSeries(
        symbol="GC=F",
        points=[PricePoint(d, c) for d, c in zip(dates, gold_closes)],
        as_of=dates[-1],
        stale=False,
    )
    macro_fx = MacroSeries(
        symbol="USDTWD=X",
        points=[PricePoint(d, c) for d, c in zip(dates, fx_closes)],
        as_of=dates[-1],
        stale=False,
    )
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 9, 5, tzinfo=UTC),
    )
    passbook = payload["targets"]["XAUUSD"]["passbook"]
    expected_latest = gold_closes[-1] * fx_closes[-1] / 31.1034768
    assert passbook["latest_price"] == pytest.approx(expected_latest)
    assert passbook["unit"] == "NT$/公克（試算）"
    # Flat FX and a steadily rising USD gold price mean the NTD series is also
    # steadily rising, so today's close should sit at the top of its own
    # 30-day range regardless of the ounce-to-gram scaling.
    assert passbook["relative_position"]["position"] == pytest.approx(1.0)
    assert passbook["pullback_stage"]["pullback_pct"] == pytest.approx(0.0)
    assert passbook["technical_score"] is not None


def test_build_payload_flags_stale_macro_context():
    macro_gold = MacroSeries(symbol="GC=F", points=[], as_of="2026-08-19", stale=True)
    macro_fx = MacroSeries(
        symbol="USDTWD=X", points=[], as_of="2026-08-19", stale=False
    )
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    assert payload["macro_context"]["stale"] is True
    # No gold points means no international-gold target can be built.
    assert "XAUUSD" not in payload["targets"]


def test_build_payload_includes_divergence():
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    divergence = payload["macro_context"]["divergence"]
    assert divergence["checked_days"] == 5
    assert divergence["same_direction_days"] == 5  # both series rise every day here
    assert divergence["is_divergent"] is True


def test_render_html_embeds_valid_json():
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    html = render_html(payload)

    assert "const SITE_DATA = " in html
    start = html.index("const SITE_DATA = ") + len("const SITE_DATA = ")
    end = html.index(";\n", start)
    embedded = json.loads(html[start:end])
    assert embedded["targets"]["00635U"]["code"] == "00635U"


def test_render_html_includes_price_charts():
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    html = render_html(payload)

    # These assert the specific call sites exist, not just that the string
    # "renderSparkline" appears somewhere in the template — a template
    # constant containing the word would make this test pass even if the
    # actual rendering calls were deleted. Asserting the exact call-site
    # source text is immune to that.
    assert "renderSparkline(target.chart" in html
    assert "renderSparkline(fxPoints" in html


def test_render_html_includes_new_indicator_cards_and_divergence():
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    html = render_html(payload)

    assert 'indicatorCard("volatility_squeeze"' in html
    assert 'indicatorCard("relative_position"' in html
    assert "macro.divergence" in html


def test_render_html_splits_indicators_by_horizon_tab():
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    html = render_html(payload)

    # Short-horizon cards.
    assert 'indicatorCard("rsi14"' in html
    assert 'indicatorCard("bollinger"' in html
    assert 'indicatorCard("volatility_squeeze"' in html
    assert 'indicatorCard("relative_position"' in html
    # Long-horizon cards.
    assert 'indicatorCard("ma20"' in html
    assert 'indicatorCard("macd"' in html
    # The horizon-tab mechanism and macro-card visibility toggle itself.
    assert "renderHorizonTabs" in html
    assert 'currentHorizon === "short"' in html
    assert "updateMacroVisibility" in html


def test_render_html_has_no_candidates_section():
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    html = render_html(payload)

    assert "candidates-card" not in html
    assert "renderCandidates" not in html


def test_build_payload_includes_pullback_stage():
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    ps = payload["targets"]["00635U"]["indicators"]["pullback_stage"]
    assert ps is not None
    assert "stage" in ps
    assert "pullback_pct" in ps
    assert "hint" in ps


def test_render_html_includes_discipline_card():
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    html = render_html(payload)

    assert "renderDisciplineCard(target)" in html
    assert "回檔 3～4% 開始第一筆 40%" in html
    assert "不要無限攤平" in html
    assert "buy-hint" in html
    assert '"提示：" + ps.hint' in html


def test_render_html_includes_position_gauge_and_auto_refresh():
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    html = render_html(payload)

    assert "renderPositionGauge(rp)" in html
    assert "gauge-track" in html
    assert "refreshSiteData" in html
    assert "SITE_DATA.auto_refresh_seconds" in html


def test_build_payload_includes_technical_score():
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    ts = payload["targets"]["00635U"]["indicators"]["technical_score"]
    assert ts is not None
    assert "composite" in ts
    assert "label" in ts
    assert set(ts["scores"].keys()) == {"rsi14", "bollinger", "ma20", "macd"}


def test_build_payload_uses_realtime_quote_when_available():
    macro_gold, macro_fx = _sample_macro()
    realtime = RealtimeQuote(
        code="00635U",
        price=99.99,
        previous_close=46.0,
        quote_date="20260820",
        quote_time="13:25:00",
    )
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
        twse_realtime={"00635U": realtime},
    )
    latest = payload["targets"]["00635U"]["latest"]
    assert latest["close"] == 99.99
    assert latest["is_realtime"] is True
    assert latest["quote_time"] == "13:25:00"


def test_build_payload_falls_back_to_daily_close_when_realtime_missing():
    macro_gold, macro_fx = _sample_macro()
    bars = _sample_bars()
    payload = build_payload(
        {"00635U": bars},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
        twse_realtime={"00635U": None},
    )
    latest = payload["targets"]["00635U"]["latest"]
    assert latest["close"] == bars[-1].close
    assert latest["is_realtime"] is False


def test_build_payload_uses_realtime_quote_for_intl_gold():
    macro_gold, macro_fx = _sample_macro()
    intl_realtime = LatestQuote(price=2077.7, quote_time="2026-08-20 13:25")
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
        intl_gold_realtime=intl_realtime,
    )
    latest = payload["targets"]["XAUUSD"]["latest"]
    assert latest["close"] == 2077.7
    assert latest["is_realtime"] is True


def test_render_html_includes_score_panel_and_realtime_price():
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    html = render_html(payload)

    assert "renderTechnicalScore(target)" in html
    assert "score-composite" in html
    assert "target.latest.is_realtime" in html
    assert "live-dot" in html


def test_build_payload_omits_ai_summary_without_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
        cache_dir=tmp_path,
    )
    assert payload["targets"]["00635U"]["ai_summary"] is None


def test_render_html_includes_ai_summary_render_hook():
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    html = render_html(payload)

    assert "renderAiSummary(target)" in html
    assert "ai-summary-card" in html


def test_fetch_bars_with_fallback_uses_prior_month_when_current_month_has_no_data(
    monkeypatch,
):
    # TWSE STOCK_DAY raises TwseFetchError (rather than returning an empty
    # list) when a month has no published trading days yet — e.g. right at
    # the start of a new month. The fallback must still kick in instead of
    # letting that exception crash the whole build.
    prev_bars = _sample_bars()

    def fake_fetch_month(code, year, month):
        if month == 9:
            raise TwseFetchError("stat != OK")
        return prev_bars

    monkeypatch.setattr(build_site_module, "fetch_month", fake_fetch_month)
    result = build_site_module._fetch_bars_with_fallback("00635U", 2026, 9)
    assert result == prev_bars


def test_fetch_bars_with_fallback_chains_back_multiple_months_when_needed():
    # Right on the 1st of a new month, the current month has zero data and
    # even the immediately prior month alone (~20 TWSE trading days) isn't
    # enough for the 30-day indicator window, so it must keep walking back.
    august_bars = _sample_bars()[:20]
    july_bars = _sample_bars()[:15]

    def fake_fetch_month(code, year, month):
        if month == 9:
            raise TwseFetchError("no data yet")
        if month == 8:
            return august_bars
        if month == 7:
            return july_bars
        raise AssertionError(f"unexpected month {month}")

    original = build_site_module.fetch_month
    build_site_module.fetch_month = fake_fetch_month
    try:
        result = build_site_module._fetch_bars_with_fallback("00635U", 2026, 9)
    finally:
        build_site_module.fetch_month = original
    assert result == july_bars + august_bars


def test_fetch_bars_with_fallback_skips_prior_month_when_current_has_enough():
    current_bars = _sample_bars()  # 30 bars, meets the >=30 threshold

    def fake_fetch_month(code, year, month):
        if month == 8:
            raise AssertionError("should not fall back when current month suffices")
        return current_bars

    original = build_site_module.fetch_month
    build_site_module.fetch_month = fake_fetch_month
    try:
        result = build_site_module._fetch_bars_with_fallback("00635U", 2026, 9)
    finally:
        build_site_module.fetch_month = original
    assert result == current_bars
