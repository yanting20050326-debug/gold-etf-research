import pytest

from gold_research.indicators import (
    bollinger_bands,
    exponential_moving_average,
    macd,
    relative_strength_index,
    simple_moving_average,
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
