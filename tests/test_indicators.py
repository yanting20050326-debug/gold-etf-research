import pytest

from gold_research.indicators import (
    bollinger_bands,
    divergence_flag,
    dynamic_swing_span,
    exponential_moving_average,
    macd,
    macd_golden_cross,
    momentum_stabilizing,
    prior_swing_low,
    pullback_stage,
    relative_position,
    relative_strength_index,
    simple_moving_average,
    synthetic_price_series,
    technical_score,
    trend_still_intact,
    volatility_squeeze,
)


def test_simple_moving_average_basic():
    result = simple_moving_average([1, 2, 3, 4, 5], window=3)
    assert result == [None, None, 2.0, 3.0, 4.0]


def test_simple_moving_average_insufficient_data():
    result = simple_moving_average([1, 2], window=3)
    assert result == [None, None]


def test_exponential_moving_average_seeds_with_sma():
    result = exponential_moving_average([1, 2, 3, 4, 5], window=3)
    assert result[0] is None and result[1] is None
    assert result[2] == pytest.approx(2.0)  # seed = mean(1,2,3)
    assert result[3] is not None
    assert result[4] is not None


def test_relative_strength_index_all_gains_is_100():
    closes = [float(i) for i in range(1, 17)]  # 16 個嚴格遞增收盤價
    result = relative_strength_index(closes, window=14)
    assert result[:14] == [None] * 14
    assert result[14] == pytest.approx(100.0)
    assert result[15] == pytest.approx(100.0)


def test_relative_strength_index_all_losses_is_0():
    closes = [float(20 - i) for i in range(16)]  # 16 個嚴格遞減收盤價
    result = relative_strength_index(closes, window=14)
    assert result[14] == pytest.approx(0.0)


def test_relative_strength_index_uses_wilder_smoothing_not_simple_average():
    # Mixed up/down series; window=5 chosen small enough to hand-verify by hand.
    closes = [10, 10.5, 10.2, 10.8, 11.0, 10.7, 11.3]
    result = relative_strength_index(closes, window=5)
    # Wilder seed at index 5: avg_gain=1.3/5=0.26, avg_loss=0.6/5=0.12
    assert result[5] == pytest.approx(68.42105263157895)
    # Wilder step at index 6: avg_gain=(0.26*4+0.6)/5=0.328, avg_loss=(0.12*4+0)/5=0.096
    assert result[6] == pytest.approx(4100 / 53)
    # A simple rolling-average (Cutler) RSI would give exactly 70.0 here — Wilder must differ.
    assert result[6] != pytest.approx(70.0)


def test_bollinger_bands_middle_matches_sma_and_upper_gt_lower():
    closes = [10, 12, 11, 13, 15, 14, 16, 18, 17, 19]
    bands = bollinger_bands(closes, window=5, num_std=2.0)
    sma = simple_moving_average(closes, window=5)
    assert bands["middle"] == sma
    for upper, mid, lower in zip(bands["upper"], bands["middle"], bands["lower"]):
        if mid is None:
            assert upper is None and lower is None
        else:
            assert upper > mid > lower


def test_macd_flat_price_is_near_zero():
    closes = [100.0] * 40
    result = macd(closes, fast=12, slow=26, signal=9)
    tail_macd = [v for v in result["macd"] if v is not None]
    tail_signal = [v for v in result["signal"] if v is not None]
    tail_hist = [v for v in result["histogram"] if v is not None]
    assert tail_macd and all(v == pytest.approx(0.0, abs=1e-9) for v in tail_macd)
    assert tail_signal and all(v == pytest.approx(0.0, abs=1e-9) for v in tail_signal)
    assert tail_hist and all(v == pytest.approx(0.0, abs=1e-9) for v in tail_hist)


def test_macd_returns_full_length_lists():
    closes = [float(100 + i) for i in range(40)]
    result = macd(closes)
    assert len(result["macd"]) == len(closes)
    assert len(result["signal"]) == len(closes)
    assert len(result["histogram"]) == len(closes)


def test_volatility_squeeze_flags_compression():
    # 前 16 個收盤價有明顯波動，最後 6 個完全持平 -> 近 5 日報酬標準差恰為 0。
    volatile = [90, 95, 88, 93, 85, 92, 87, 94, 89, 96, 84, 91, 86, 93, 88, 95]
    flat = [100, 100, 100, 100, 100, 100]
    closes = volatile + flat
    result = volatility_squeeze(closes, short_window=5, long_window=20)
    assert result["short_vol"] == pytest.approx(0.0)
    assert result["long_vol"] > 0
    assert result["ratio"] == pytest.approx(0.0)
    assert result["is_compressed"] is True


def test_volatility_squeeze_not_compressed_when_ratio_near_one():
    closes = [100.0]
    for i in range(24):
        closes.append(closes[-1] + (2 if i % 2 == 0 else -2))
    result = volatility_squeeze(closes, short_window=5, long_window=20)
    assert result["ratio"] == pytest.approx(1.0, abs=0.15)
    assert result["is_compressed"] is False


def test_volatility_squeeze_insufficient_data():
    result = volatility_squeeze([100.0] * 10, short_window=5, long_window=20)
    assert result == {
        "short_vol": None,
        "long_vol": None,
        "ratio": None,
        "is_compressed": False,
    }


def test_relative_position_mid_range():
    result = relative_position([10, 12, 8, 15, 11], window=5)
    assert result["position"] == pytest.approx(3 / 7)
    assert result["label"] == "區間中段"


def test_relative_position_low_and_high_labels():
    low = relative_position([10, 50, 50, 50, 8], window=5)
    assert low["position"] == pytest.approx(0.0)
    assert low["label"] == "相對低點區"

    high = relative_position([10, 10, 10, 10, 50], window=5)
    assert high["position"] == pytest.approx(1.0)
    assert high["label"] == "相對高點區"


def test_relative_position_insufficient_data():
    assert relative_position([1, 2, 3], window=5) is None


def test_divergence_flag_detects_majority_same_direction():
    dates = [f"2026-08-{d:02d}" for d in range(1, 7)]
    gold_by_date = dict(zip(dates, [100, 102, 101, 103, 102, 104]))
    fx_by_date = dict(zip(dates, [30, 31, 30, 31, 32, 31]))
    result = divergence_flag(gold_by_date, fx_by_date, window=5)
    assert result == {
        "same_direction_days": 3,
        "checked_days": 5,
        "is_divergent": True,
    }


def test_divergence_flag_not_divergent_when_mostly_opposite():
    dates = [f"2026-08-{d:02d}" for d in range(1, 7)]
    gold_by_date = dict(zip(dates, [100, 102, 101, 103, 102, 104]))
    fx_by_date = dict(zip(dates, [30, 31, 32, 33, 34, 33]))
    result = divergence_flag(gold_by_date, fx_by_date, window=5)
    assert result == {
        "same_direction_days": 2,
        "checked_days": 5,
        "is_divergent": False,
    }


def test_pullback_stage_first_tranche_zone():
    closes = [100.0] * 29 + [97.0]  # 30-day high=100, current=97 -> pullback=3%
    result = pullback_stage(closes, window=30)
    assert result["recent_high"] == 100.0
    assert result["pullback_pct"] == pytest.approx(3.0)
    assert result["stage"] == "第一筆 20%"
    assert result["hint"] == "符合第一筆進場條件，可考慮分批的第一筆 20%"


def test_pullback_stage_all_zone_boundaries():
    def make(pullback_target_pct):
        high = 100.0
        current = high * (1 - pullback_target_pct / 100)
        return [high] * 29 + [current]

    assert pullback_stage(make(1))["stage"] == "觀察區（尚未回檔到位）"
    assert pullback_stage(make(3.5))["stage"] == "第一筆 20%"
    assert pullback_stage(make(5))["stage"] == "加碼 20%（前提：趨勢沒壞）"
    assert pullback_stage(make(7))["stage"] == "加碼 20%（前提：出現止跌）"
    assert pullback_stage(make(12))["stage"] == "最後 40%（等重新轉強）"


def test_pullback_stage_threshold_scale_doubles_boundaries_for_leveraged_targets():
    def make(pullback_target_pct):
        high = 100.0
        current = high * (1 - pullback_target_pct / 100)
        return [high] * 29 + [current]

    # A 2x-leveraged product should need roughly twice the pullback % to hit
    # the same stage as its unleveraged counterpart.
    assert (
        pullback_stage(make(5), threshold_scale=2.0)["stage"]
        == "觀察區（尚未回檔到位）"
    )
    assert pullback_stage(make(7), threshold_scale=2.0)["stage"] == "第一筆 20%"
    assert (
        pullback_stage(make(10), threshold_scale=2.0)["stage"]
        == "加碼 20%（前提：趨勢沒壞）"
    )
    assert (
        pullback_stage(make(15), threshold_scale=2.0)["stage"]
        == "加碼 20%（前提：出現止跌）"
    )
    assert (
        pullback_stage(make(20), threshold_scale=2.0)["stage"]
        == "最後 40%（等重新轉強）"
    )


def test_pullback_stage_insufficient_data():
    assert pullback_stage([100.0] * 10, window=30) is None


def test_prior_swing_low_detects_still_valid_trough():
    # Descend to a clear trough at index 9 (111.0), then rally without ever
    # closing back below it — the trough is still standing support.
    closes = [120.0 - i for i in range(10)]
    closes += [111.0 + i for i in range(1, 21)]
    result = prior_swing_low(closes, window=30, swing_span=3)
    assert result["price"] == pytest.approx(111.0)
    assert result["days_ago"] == 20


def test_prior_swing_low_skips_a_breached_trough_for_a_more_recent_one():
    # A first trough at 95 later gets undercut (down to 90) before recovering
    # — 95 no longer represents standing support, so a subsequent, deeper
    # trough at 88 that nothing has broken since should be reported instead.
    closes = [100, 99, 98, 97, 96]  # decline
    closes += [95]  # trough A — later breached
    closes += [97, 99, 101]  # bounce
    closes += [98, 94, 90]  # decline again, undercutting trough A (95)
    closes += [88]  # trough B — never breached afterward
    closes += [90 + i * 2 for i in range(18)]  # steady rally, stays above 88
    result = prior_swing_low(closes, window=30, swing_span=1)
    assert result["price"] == pytest.approx(88.0)
    assert result["days_ago"] == 18


def test_prior_swing_low_returns_none_when_every_trough_has_been_broken():
    # A relentless string of ever-lower lows: whichever trough you pick,
    # something later undercuts it, so there is no still-valid reference.
    closes = [100.0 - i for i in range(35)]
    assert prior_swing_low(closes, window=30, swing_span=2) is None


def test_prior_swing_low_returns_none_without_a_local_minimum():
    # Strictly rising the whole window — no point has lower valleys on both
    # sides, so there is no well-defined swing low to report.
    closes = [100.0 + i for i in range(30)]
    assert prior_swing_low(closes, window=30, swing_span=3) is None


def test_prior_swing_low_insufficient_data():
    assert prior_swing_low([100.0] * 10, window=30) is None


def test_dynamic_swing_span_shrinks_when_recent_volatility_is_compressed():
    # 前 16 天有明顯波動，最後 5 天完全持平 -> 近期波動率遠低於基準，
    # span 應該縮小，比較能抓到淺一點的回檔。
    volatile = [100, 95, 105, 92, 108, 90, 110, 88, 112, 86, 114, 84, 116, 82, 118, 96]
    flat = [118, 118, 118, 118, 118]
    closes = volatile + flat
    span = dynamic_swing_span(closes, base_span=3, min_span=1, max_span=6)
    assert span < 3


def test_dynamic_swing_span_grows_when_recent_volatility_is_expanding():
    # 前 16 天持平，最後 5 天劇烈震盪 -> 近期波動率遠高於基準，
    # span 應該放大，避免把正常的日內雜訊誤判成起漲低點。
    flat = [100.0] * 16
    volatile = [100, 90, 110, 80, 120]
    closes = flat + volatile
    span = dynamic_swing_span(closes, base_span=3, min_span=1, max_span=6)
    assert span > 3


def test_dynamic_swing_span_falls_back_to_base_with_insufficient_data():
    # 不夠 20 天算不出 volatility_squeeze 的 ratio，直接用基準值。
    span = dynamic_swing_span([100.0] * 10, base_span=3)
    assert span == 3


def test_dynamic_swing_span_respects_bounds():
    volatile = [100, 95, 105, 92, 108, 90, 110, 88, 112, 86, 114, 84, 116, 82, 118, 96]
    flat = [118, 118, 118, 118, 118]
    closes = volatile + flat
    span = dynamic_swing_span(closes, base_span=3, min_span=2, max_span=6)
    assert span >= 2


def test_prior_swing_low_uses_dynamic_span_when_volatility_recently_compressed():
    # 「兩次回檔、中間小反彈」的結構：一次比較深、比較舊（70），
    # 一次比較淺、比較新（75），但固定 span=3 的比較窗格會「跨過」
    # 中間的小反彈，選到比較舊、比較深的低點。真實案例（00635U）
    # 就是這樣：近期波動率轉穩定時，動態 span 縮小，才會改抓到比較
    # 新、更貼近這波真正起漲點的低點。
    decline = [100 - i * 2 for i in range(15)]  # 100 一路跌到 72
    deep_dip = [70]  # 比較深、比較舊的低點
    bounce = [76, 78]  # 小反彈
    shallow_dip = [75]  # 比較淺、比較新的低點
    rally = [75.0 + i * 3 for i in range(1, 12)]  # 波動壓縮、平穩漲到新高 108
    closes = decline + deep_dip + bounce + shallow_dip + rally
    fixed_span3 = prior_swing_low(closes, window=30, swing_span=3)
    dynamic = prior_swing_low(closes, window=30)
    assert fixed_span3["price"] == pytest.approx(70.0)
    assert dynamic["price"] == pytest.approx(75.0)
    assert dynamic["days_ago"] < fixed_span3["days_ago"]


def test_trend_still_intact_true_when_above_both_references():
    assert trend_still_intact(close=105.0, ma20=100.0, prior_low_price=95.0) is True


def test_trend_still_intact_false_when_below_ma20():
    assert trend_still_intact(close=98.0, ma20=100.0, prior_low_price=95.0) is False


def test_trend_still_intact_false_when_below_prior_low():
    assert trend_still_intact(close=94.0, ma20=90.0, prior_low_price=95.0) is False


def test_trend_still_intact_none_when_inputs_missing():
    assert trend_still_intact(close=100.0, ma20=None, prior_low_price=95.0) is None
    assert trend_still_intact(close=100.0, ma20=90.0, prior_low_price=None) is None


def test_momentum_stabilizing_true_when_rsi_recovers_without_new_low():
    closes = [100, 98, 96, 94, 92, 90, 89, 90, 91]
    assert momentum_stabilizing(closes, rsi_window=5, lookback=3) is True


def test_momentum_stabilizing_false_when_still_making_new_lows():
    closes = [100, 98, 96, 94, 92, 90, 89, 87, 85]
    assert momentum_stabilizing(closes, rsi_window=5, lookback=3) is False


def test_momentum_stabilizing_none_with_insufficient_data():
    assert momentum_stabilizing([100.0] * 5, rsi_window=14, lookback=3) is None


def test_macd_golden_cross_true_on_fresh_flip_to_positive():
    # Accelerating decline (keeps histogram clearly negative for a while),
    # then a sharp V-reversal in the last few days flips it positive.
    closes = [200.0]
    for i in range(1, 45):
        closes.append(closes[-1] - min(0.2 + i * 0.15, 6.0))
    bottom = closes[-1]
    closes += [bottom + 3, bottom + 8, bottom + 15]
    assert macd_golden_cross(closes, lookback=3) is True


def test_macd_golden_cross_false_when_already_established():
    # Same setup, but the rally has run long enough that the cross happened
    # well outside the lookback window — no longer a *fresh* signal.
    closes = [200.0]
    for i in range(1, 45):
        closes.append(closes[-1] - min(0.2 + i * 0.15, 6.0))
    bottom = closes[-1]
    closes += [
        bottom + 3,
        bottom + 8,
        bottom + 15,
        bottom + 20,
        bottom + 24,
        bottom + 27,
    ]
    assert macd_golden_cross(closes, lookback=3) is False


def test_macd_golden_cross_none_with_insufficient_data():
    assert macd_golden_cross([100.0] * 10, lookback=3) is None


def test_technical_score_computes_composite_and_label():
    result = technical_score(
        close=48.10,
        ma20=45.05,
        rsi14=75.78,
        bollinger={"upper": 48.85, "lower": 41.26},
        macd_value=1.3349,
    )
    assert result["scores"]["rsi14"] == pytest.approx(24.22, abs=0.01)
    assert result["scores"]["bollinger"] == pytest.approx(9.89, abs=0.05)
    assert result["scores"]["ma20"] == pytest.approx(16.14, abs=0.05)
    assert result["scores"]["macd"] == pytest.approx(22.25, abs=0.01)
    assert result["composite"] == pytest.approx(18.13, abs=0.05)
    assert result["label"] == "技術面偏熱"


def test_technical_score_neutral_band():
    result = technical_score(
        close=100.0,
        ma20=100.0,
        rsi14=50.0,
        bollinger={"upper": 110.0, "lower": 90.0},
        macd_value=0.0,
    )
    assert result["composite"] == pytest.approx(50.0)
    assert result["label"] == "技術面中性"


def test_technical_score_cold_band():
    result = technical_score(
        close=90.0,
        ma20=100.0,
        rsi14=10.0,
        bollinger={"upper": 110.0, "lower": 90.0},
        macd_value=-2.0,
    )
    assert result["label"] == "技術面偏冷"


def test_technical_score_returns_none_when_no_inputs_available():
    assert (
        technical_score(
            close=100.0,
            ma20=None,
            rsi14=None,
            bollinger={"upper": None, "lower": None},
            macd_value=None,
        )
        is None
    )


def test_technical_score_handles_partial_inputs():
    result = technical_score(
        close=100.0,
        ma20=None,
        rsi14=20.0,
        bollinger={"upper": None, "lower": None},
        macd_value=None,
    )
    assert set(result["scores"].keys()) == {"rsi14"}
    assert result["composite"] == pytest.approx(80.0)


def test_divergence_flag_excludes_zero_change_days():
    dates = ["2026-08-01", "2026-08-02", "2026-08-03"]
    gold_by_date = dict(zip(dates, [100, 100, 105]))
    fx_by_date = dict(zip(dates, [30, 31, 32]))
    result = divergence_flag(gold_by_date, fx_by_date, window=5)
    assert result == {
        "same_direction_days": 1,
        "checked_days": 1,
        "is_divergent": False,
    }


def test_synthetic_price_series_multiplies_matching_dates():
    gold_by_date = {"2026-08-01": 2000.0, "2026-08-02": 2010.0, "2026-08-03": 2020.0}
    fx_by_date = {"2026-08-01": 31.0, "2026-08-02": 31.5, "2026-08-03": 32.0}
    result = synthetic_price_series(gold_by_date, fx_by_date)
    assert result == [
        pytest.approx(62000.0),
        pytest.approx(63315.0),
        pytest.approx(64640.0),
    ]


def test_synthetic_price_series_sorts_by_date_regardless_of_dict_order():
    gold_by_date = {"2026-08-03": 2020.0, "2026-08-01": 2000.0, "2026-08-02": 2010.0}
    fx_by_date = {"2026-08-02": 31.5, "2026-08-03": 32.0, "2026-08-01": 31.0}
    result = synthetic_price_series(gold_by_date, fx_by_date)
    assert result == [
        pytest.approx(62000.0),
        pytest.approx(63315.0),
        pytest.approx(64640.0),
    ]


def test_synthetic_price_series_excludes_dates_missing_on_either_side():
    gold_by_date = {"2026-08-01": 2000.0, "2026-08-02": 2010.0}
    fx_by_date = {"2026-08-01": 31.0, "2026-08-03": 32.0}
    result = synthetic_price_series(gold_by_date, fx_by_date)
    assert result == [pytest.approx(62000.0)]


def test_synthetic_price_series_empty_when_no_common_dates():
    result = synthetic_price_series({"2026-08-01": 2000.0}, {"2026-08-02": 31.0})
    assert result == []
