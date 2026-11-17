import json
from datetime import UTC, datetime, timedelta

from gold_research import build_site as build_site_module
from gold_research.build_site import build_payload, render_html
from gold_research.fetch_macro import LatestQuote, MacroSeries, PricePoint
from gold_research.fetch_tbb_gold import PassbookDay, PassbookQuote, TbbGoldFetchError
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
    assert payload["live_refresh_seconds"] == 15
    assert payload["live_quotes_url"].startswith("https://")
    target = payload["targets"]["00635U"]
    assert target["asset_class"] == "commodity_futures_etf"
    assert target["indicators"]["ma20"] is not None
    assert target["indicators"]["rsi14"] is not None
    assert len(target["chart"]) == 30
    signals = target["indicators"]["stage_signals"]
    assert set(signals.keys()) == {
        "trend_intact",
        "momentum_stabilizing",
        "macd_golden_cross",
    }
    assert "candidates" not in payload


def test_build_payload_includes_multiple_twse_targets_and_intl_gold():
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars(46.0), "00708L": _sample_bars(20.0)},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )

    assert set(payload["targets"].keys()) == {"00635U", "00708L", "GCF"}
    assert payload["targets"]["00708L"]["asset_class"] == "leveraged_futures_etf"
    # 00708L is a 2x-leveraged product, so its pullback-stage thresholds
    # should be doubled relative to the unleveraged 00635U and GCF.
    assert payload["targets"]["00635U"]["pullback_scale"] == 1.0
    assert payload["targets"]["00708L"]["pullback_scale"] == 2.0
    intl_gold = payload["targets"]["GCF"]
    assert intl_gold["asset_class"] == "commodity_futures"
    assert intl_gold["pullback_scale"] == 1.0
    assert intl_gold["quote_delay_note"]
    # 6 macro fixture points isn't enough for the 30-day relative_position
    # window; it's expected to be None, not the schema key being missing.
    assert "relative_position" in intl_gold["indicators"]
    assert intl_gold["latest"]["close"] == macro_gold.points[-1].close
    # 換算出來的「試算」存摺價已移除，改用台企銀真實牌價（TBBGOLD 標的）。
    assert "passbook" not in intl_gold


def _tbb_series(n: int = 40, start: float = 4000.0) -> list[tuple[str, float]]:
    first = datetime(2026, 7, 1, tzinfo=UTC)
    return [
        ((first + timedelta(days=i)).strftime("%Y-%m-%d"), start + i * 2.0)
        for i in range(n)
    ]


def _tbb_quote(series, sell=4100.0, buy=4054.0, same_day=True) -> PassbookQuote:
    last_date = series[-1][0]
    if not same_day:
        last_date = (
            datetime.strptime(last_date, "%Y-%m-%d").replace(tzinfo=UTC)
            + timedelta(days=1)
        ).strftime("%Y-%m-%d")
    return PassbookQuote(
        sell=sell, buy=buy, listed_date=last_date, listed_time="10:08:37"
    )


def test_build_payload_adds_tbb_passbook_target_using_sell_price(tmp_path):
    macro_gold, macro_fx = _sample_macro()
    series = _tbb_series()
    quote = _tbb_quote(series)
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
        cache_dir=tmp_path,
        tbb_series=series,
        tbb_quote=quote,
    )
    tbb = payload["targets"]["TBBGOLD"]
    assert tbb["display_name"] == "台企銀黃金存摺"
    assert tbb["price_digits"] == 0
    # 價格、回檔高點、指標都以「賣出價」序列為準；當日牌價覆蓋最後一天。
    assert tbb["latest"]["close"] == quote.sell
    assert tbb["latest"]["prev_close"] == series[-2][1]
    assert tbb["latest"]["is_realtime"] is False
    assert tbb["chart"][-1] == {"trading_date": series[-1][0], "close": quote.sell}
    assert len(tbb["chart"]) == len(series)
    assert tbb["indicators"]["pullback_stage"]["recent_high"] == quote.sell
    assert tbb["data_source"]["as_of"] == series[-1][0]
    assert tbb["passbook_quote"] == {
        "sell": 4100.0,
        "buy": 4054.0,
        "spread": 46.0,
        "listed_at": f"{series[-1][0]} 10:08:37",
    }


def test_tbb_target_omitted_when_series_missing_or_too_short(tmp_path):
    macro_gold, macro_fx = _sample_macro()
    common = {"cache_dir": tmp_path}
    no_series = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
        **common,
    )
    short = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
        tbb_series=_tbb_series(10),
        **common,
    )
    assert "TBBGOLD" not in no_series["targets"]
    assert "TBBGOLD" not in short["targets"]


def test_tbb_target_without_quote_has_no_passbook_quote(tmp_path):
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
        cache_dir=tmp_path,
        tbb_series=_tbb_series(),
    )
    assert "passbook_quote" not in payload["targets"]["TBBGOLD"]


def test_apply_quote_to_series_replaces_same_day_and_appends_new_day():
    series = _tbb_series(5)
    same = build_site_module.apply_quote_to_series(
        series, _tbb_quote(series, sell=9999.0)
    )
    assert same[:-1] == series[:-1]
    assert same[-1] == (series[-1][0], 9999.0)

    nxt_quote = _tbb_quote(series, sell=5000.0, same_day=False)
    appended = build_site_module.apply_quote_to_series(series, nxt_quote)
    assert len(appended) == len(series) + 1
    assert appended[-1] == (nxt_quote.listed_date, 5000.0)

    stale = PassbookQuote(
        sell=1.0, buy=1.0, listed_date="2000-01-01", listed_time="09:00:00"
    )
    assert build_site_module.apply_quote_to_series(series, stale) == series
    assert build_site_module.apply_quote_to_series(series, None) == series


def _published_tbb(series) -> dict:
    return {"chart": [{"trading_date": d, "close": c} for d, c in series]}


def test_resolve_tbb_inputs_reuses_published_history_when_it_covers_today():
    series = _tbb_series()
    quote = _tbb_quote(series)

    def history_must_not_be_called():
        raise AssertionError("已發布的歷史涵蓋今天，不該再抓歷史牌價")

    result, got_quote = build_site_module.resolve_tbb_inputs(
        _published_tbb(series),
        fetch_quote=lambda: quote,
        fetch_history_days=history_must_not_be_called,
    )
    assert result == series
    assert got_quote == quote


def test_resolve_tbb_inputs_fetches_history_on_a_new_day():
    series = _tbb_series()
    quote = _tbb_quote(series, same_day=False)
    days = [PassbookDay(trading_date=d, buy=c - 46, sell=c) for d, c in series] + [
        PassbookDay(trading_date=quote.listed_date, buy=quote.buy, sell=quote.sell)
    ]
    result, _ = build_site_module.resolve_tbb_inputs(
        _published_tbb(series),
        fetch_quote=lambda: quote,
        fetch_history_days=lambda: days,
    )
    assert result[-1] == (quote.listed_date, quote.sell)
    assert len(result) == len(series) + 1


def test_resolve_tbb_inputs_survives_failures():
    series = _tbb_series()

    def quote_fails():
        raise TbbGoldFetchError("down")

    def history_fails():
        raise TbbGoldFetchError("down")

    # 當日牌價抓不到：沿用已發布的序列，不丟例外。
    result, quote = build_site_module.resolve_tbb_inputs(
        _published_tbb(series),
        fetch_quote=quote_fails,
        fetch_history_days=history_fails,
    )
    assert result == series and quote is None

    # 第一次執行、什麼都沒有、歷史又抓不到：回 None，由呼叫端略過這個標的。
    result, quote = build_site_module.resolve_tbb_inputs(
        None, fetch_quote=quote_fails, fetch_history_days=history_fails
    )
    assert result is None and quote is None

    # 歷史抓不到但有舊序列：沿用舊序列。
    new_day = _tbb_quote(series, same_day=False)
    result, _ = build_site_module.resolve_tbb_inputs(
        _published_tbb(series),
        fetch_quote=lambda: new_day,
        fetch_history_days=history_fails,
    )
    assert result == series


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
    assert "GCF" not in payload["targets"]


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
    assert "target.chart.slice(-60)" in html
    assert "renderSparkline(points" in html
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

    assert 'key: "volatility_squeeze"' in html
    assert 'key: "relative_position"' in html
    assert "macro.divergence" in html


def test_render_html_shows_all_six_indicators_together():
    # Horizon tabs were removed: the discipline card's own signal checks
    # (trend_intact needs MA20, macd_golden_cross needs MACD) reference
    # indicators regardless of which "horizon" a user might have been on,
    # so splitting the indicator grid by tab just hid the numbers behind
    # those checks. All six cards now render unconditionally together.
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    html = render_html(payload)

    for key in (
        "rsi14",
        "bollinger",
        "volatility_squeeze",
        "relative_position",
        "ma20",
        "macd",
    ):
        assert f'key: "{key}"' in html
    # 每個指標都要有一句白話說明，且用色帶標出「數字大小落在哪一區」。
    assert html.count("plain:") >= 6
    assert "renderIndicatorCard(spec)" in html
    assert "makeZoneBar(spec.zones, spec.pos)" in html
    # The old horizon-tab mechanism should be gone entirely, not just unused.
    assert "renderHorizonTabs" not in html
    assert "currentHorizon" not in html
    assert "updateMacroVisibility" not in html


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
    assert "disciplineRules(scale)" in html
    assert "開始第一筆 20%。" in html
    assert "不要無限攤平" in html
    assert "ps.hint" in html
    assert "現在該做什麼？" in html


def test_render_html_includes_position_gauge_and_auto_refresh():
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    html = render_html(payload)

    assert "makeZoneBar(" in html
    assert "zonemark" in html
    assert "refreshSiteData" in html
    assert "SITE_DATA.auto_refresh_seconds" in html
    assert "refreshLiveQuotes" in html
    assert "SITE_DATA.live_refresh_seconds" in html
    assert "data/live_quotes.json" in html
    # 優先用 Worker、失敗才退回靜態 JSON。
    assert "SITE_DATA.live_quotes_url" in html
    assert "fetchLiveQuotes" in html
    assert 'id="page-updated-time"' in html
    assert "markPageUpdatedNow()" in html


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


def test_latest_prev_close_comes_from_twse_previous_close_when_realtime():
    macro_gold, macro_fx = _sample_macro()
    realtime = RealtimeQuote(
        code="00635U",
        price=49.5,
        previous_close=49.0,
        quote_date="20260820",
        quote_time="10:00:00",
    )
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
        twse_realtime={"00635U": realtime},
    )
    assert payload["targets"]["00635U"]["latest"]["prev_close"] == 49.0


def test_latest_prev_close_falls_back_to_previous_daily_bar():
    macro_gold, macro_fx = _sample_macro()
    bars = _sample_bars()
    payload = build_payload(
        {"00635U": bars},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    assert payload["targets"]["00635U"]["latest"]["prev_close"] == bars[-2].close
    gcf = payload["targets"]["GCF"]
    assert gcf["latest"]["prev_close"] == macro_gold.points[-2].close


def test_ai_summary_field_carries_generated_date(tmp_path):
    cache = tmp_path / "ai_summary_00635U.json"
    cache.write_text(
        json.dumps({"text": "快取的摘要", "generated_date": "2026-08-20"}),
        encoding="utf-8",
    )
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
        cache_dir=tmp_path,
    )
    summary = payload["targets"]["00635U"]["ai_summary"]
    assert summary["text"] == "快取的摘要"
    assert summary["generated_date"] == "2026-08-20"


def test_prior_low_broken_means_trend_is_not_intact_rather_than_unknown():
    # 一路創新低：過去的每個低點都被後面的收盤價跌破，所以找不到有效前低。
    # 這要標成「已跌破」，趨勢沒壞必須是 False，不能是「資料不足」(None)。
    closes: list[float] = []
    low = 100.0
    for _ in range(6):  # 每一波低點都比上一波低，結尾再跌破最近一個低點
        closes += [low + 6, low + 4, low + 2, low, low + 2, low + 4]
        low -= 4
    closes += [low + 2, low + 1, low]
    start = datetime(2026, 7, 1, tzinfo=UTC)
    bars = [
        DailyBar(
            trading_date=(start + timedelta(days=i)).strftime("%Y-%m-%d"),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=1_000,
        )
        for i, c in enumerate(closes)
    ]
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": bars}, macro_gold, macro_fx, datetime(2026, 8, 20, tzinfo=UTC)
    )
    ind = payload["targets"]["00635U"]["indicators"]
    assert ind["prior_low"] is None
    assert ind["prior_low_broken"] is True
    assert ind["stage_signals"]["trend_intact"] is False


def test_prior_low_broken_is_false_in_an_uptrend():
    # 一路墊高的行情：低點沒有被跌破，不能顯示「已跌破所有前低」。
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    assert payload["targets"]["00635U"]["indicators"]["prior_low_broken"] is False


def test_payload_points_the_assistant_at_the_worker_chat_route():
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    assert payload["assistant_url"] == payload["live_quotes_url"] + "chat"
    assert payload["assistant_url"].startswith("https://")


def test_render_html_includes_the_assistant_chat_widget():
    macro_gold, macro_fx = _sample_macro()
    payload = build_payload(
        {"00635U": _sample_bars()},
        macro_gold,
        macro_fx,
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    html = render_html(payload)

    assert "initAssistant()" in html
    assert "SITE_DATA.assistant_url" in html
    assert "sendAssistantMessage" in html
    # 金鑰只在 Worker 端：網頁不能出現任何金鑰字樣或帶 key= 的網址。
    assert "x-goog-api-key" not in html
    assert "key=" not in html
    # 沒啟用、被限流時要有明確的說明，不能默默失敗。
    assert "小幫手還沒啟用" in html
    assert "今天的提問次數已達上限" in html
    # 失敗的那一輪不能被帶進下一次對話的上下文。
    assert "userMessage.skip = true" in html


def test_dashboard_template_file_exists_and_has_data_placeholder():
    template = build_site_module.DASHBOARD_TEMPLATE_PATH.read_text(encoding="utf-8")
    assert template.count("__DATA__") == 1


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
    latest = payload["targets"]["GCF"]["latest"]
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
    assert "score-big" in html
    assert "priceInfo(target)" in html
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
    # 沒有摘要時要明講，不能讓區塊悄悄消失（之前就是這樣讓人以為只有國際盤有）。
    assert "今日摘要暫時無法產生" in html
    assert "summary.generated_date" in html


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


def _taipei_datetime(year, month, day, hour, minute) -> datetime:
    # Asia/Taipei is a fixed UTC+8 offset (no DST), so building the UTC
    # instant directly avoids depending on the system tzdata database.
    return datetime(year, month, day, hour, minute, tzinfo=UTC) - timedelta(hours=8)


def test_is_twse_trading_session_true_during_session_on_a_weekday():
    # 2026-09-01 is a Tuesday; 10:00 Taipei time is inside the trading window.
    now = _taipei_datetime(2026, 9, 1, 10, 0)
    assert build_site_module._is_twse_trading_session(now) is True


def test_is_twse_trading_session_false_before_session_opens():
    now = _taipei_datetime(2026, 9, 1, 7, 0)
    assert build_site_module._is_twse_trading_session(now) is False


def test_is_twse_trading_session_false_after_session_closes():
    now = _taipei_datetime(2026, 9, 1, 20, 0)
    assert build_site_module._is_twse_trading_session(now) is False


def test_is_twse_trading_session_false_on_weekend():
    # 2026-09-05 is a Saturday, even though the time-of-day is mid-session.
    now = _taipei_datetime(2026, 9, 5, 10, 0)
    assert build_site_module._is_twse_trading_session(now) is False


def test_fetch_published_site_data_returns_empty_dict_on_network_error(monkeypatch):
    import requests

    def raise_connection_error(url, timeout):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(build_site_module.requests, "get", raise_connection_error)
    assert build_site_module._fetch_published_site_data() == {}


def test_fetch_published_site_data_returns_empty_dict_on_non_200(monkeypatch):
    class FakeResponse:
        status_code = 404

    monkeypatch.setattr(
        build_site_module.requests, "get", lambda url, timeout: FakeResponse()
    )
    assert build_site_module._fetch_published_site_data() == {}


def test_fetch_published_site_data_returns_parsed_json_on_success(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {"targets": {"00635U": {"code": "00635U"}}}

    monkeypatch.setattr(
        build_site_module.requests, "get", lambda url, timeout: FakeResponse()
    )
    assert build_site_module._fetch_published_site_data() == {
        "targets": {"00635U": {"code": "00635U"}}
    }
