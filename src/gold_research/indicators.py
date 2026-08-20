"""黃金相關研究站的技術指標純函式。

所有函式輸入一串照時間順序排列的收盤價，回傳跟輸入等長的序列；
資料不足以計算的位置一律填 None，不做外插或假設。
"""

from __future__ import annotations


def simple_moving_average(closes: list[float], window: int) -> list[float | None]:
    result: list[float | None] = [None] * len(closes)
    for i in range(window - 1, len(closes)):
        result[i] = sum(closes[i - window + 1 : i + 1]) / window
    return result


def exponential_moving_average(closes: list[float], window: int) -> list[float | None]:
    result: list[float | None] = [None] * len(closes)
    if len(closes) < window:
        return result
    multiplier = 2 / (window + 1)
    seed = sum(closes[:window]) / window
    result[window - 1] = seed
    prev = seed
    for i in range(window, len(closes)):
        prev = (closes[i] - prev) * multiplier + prev
        result[i] = prev
    return result


def relative_strength_index(
    closes: list[float], window: int = 14
) -> list[float | None]:
    result: list[float | None] = [None] * len(closes)
    if len(closes) <= window:
        return result
    gains = [0.0] * len(closes)
    losses = [0.0] * len(closes)
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains[i] = max(change, 0.0)
        losses[i] = max(-change, 0.0)
    avg_gain = sum(gains[1 : window + 1]) / window
    avg_loss = sum(losses[1 : window + 1]) / window
    result[window] = _rsi_from_averages(avg_gain, avg_loss)
    for i in range(window + 1, len(closes)):
        avg_gain = (avg_gain * (window - 1) + gains[i]) / window
        avg_loss = (avg_loss * (window - 1) + losses[i]) / window
        result[i] = _rsi_from_averages(avg_gain, avg_loss)
    return result


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def bollinger_bands(
    closes: list[float], window: int = 20, num_std: float = 2.0
) -> dict[str, list[float | None]]:
    middle = simple_moving_average(closes, window)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    for i in range(window - 1, len(closes)):
        segment = closes[i - window + 1 : i + 1]
        mean = middle[i]
        variance = sum((x - mean) ** 2 for x in segment) / window
        std = variance**0.5
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std
    return {"upper": upper, "middle": middle, "lower": lower}


def macd(
    closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> dict[str, list[float | None]]:
    ema_fast = exponential_moving_average(closes, fast)
    ema_slow = exponential_moving_average(closes, slow)
    macd_line: list[float | None] = [
        None if a is None or b is None else a - b for a, b in zip(ema_fast, ema_slow)
    ]
    macd_values = [v for v in macd_line if v is not None]
    signal_start = len(closes) - len(macd_values)
    signal_ema = exponential_moving_average(macd_values, signal)
    signal_line: list[float | None] = [None] * len(closes)
    for offset, value in enumerate(signal_ema):
        signal_line[signal_start + offset] = value
    histogram: list[float | None] = [
        None if a is None or b is None else a - b
        for a, b in zip(macd_line, signal_line)
    ]
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}
