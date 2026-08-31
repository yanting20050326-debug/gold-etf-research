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


def _stdev(values: list[float]) -> float:
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return variance**0.5


def volatility_squeeze(
    closes: list[float], short_window: int = 5, long_window: int = 20
) -> dict[str, float | bool | None]:
    """短期／長期波動度比值。比值明顯偏低代表近期盤整壓縮，常是變盤前兆（不代表方向）。"""
    if len(closes) <= long_window:
        return {
            "short_vol": None,
            "long_vol": None,
            "ratio": None,
            "is_compressed": False,
        }
    returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))
    ]
    short_vol = _stdev(returns[-short_window:])
    long_vol = _stdev(returns[-long_window:])
    ratio = None if long_vol == 0 else short_vol / long_vol
    is_compressed = ratio is not None and ratio < 0.5
    return {
        "short_vol": short_vol,
        "long_vol": long_vol,
        "ratio": ratio,
        "is_compressed": is_compressed,
    }


def relative_position(
    closes: list[float], window: int = 30
) -> dict[str, float | str] | None:
    """今天收盤價在近 window 天最高最低區間中的相對位置（0~1）與白話標籤。"""
    if len(closes) < window:
        return None
    segment = closes[-window:]
    lowest = min(segment)
    highest = max(segment)
    position = 0.5 if highest == lowest else (closes[-1] - lowest) / (highest - lowest)
    if position < 0.2:
        label = "相對低點區"
    elif position > 0.8:
        label = "相對高點區"
    else:
        label = "區間中段"
    return {"position": position, "label": label}


_STAGE_HINTS = {
    "觀察區（尚未回檔到位）": "尚未回檔到位，暫不建議進場",
    "第一筆 20%": "符合第一筆進場條件，可考慮分批的第一筆 20%",
    "加碼 20%（前提：趨勢沒壞）": "符合加碼條件，若趨勢未壞可加碼 20%",
    "加碼 20%（前提：出現止跌）": "符合加碼條件，若已出現止跌可再加碼 20%",
    "最後 40%（等重新轉強）": "已達較大回檔，最後 40% 務必等重新轉強才進場，避免無限攤平",
}


def pullback_stage(
    closes: list[float], window: int = 30
) -> dict[str, float | str] | None:
    """近 window 天高點以來的回檔幅度，對照分批進場紀律標出目前處於哪個階段。"""
    if len(closes) < window:
        return None
    segment = closes[-window:]
    recent_high = max(segment)
    current = closes[-1]
    pullback_pct = (
        0.0 if recent_high == 0 else (recent_high - current) / recent_high * 100
    )
    if pullback_pct < 3:
        stage = "觀察區（尚未回檔到位）"
    elif pullback_pct < 4:
        stage = "第一筆 20%"
    elif pullback_pct < 6:
        stage = "加碼 20%（前提：趨勢沒壞）"
    elif pullback_pct < 9:
        stage = "加碼 20%（前提：出現止跌）"
    else:
        stage = "最後 40%（等重新轉強）"
    return {
        "recent_high": recent_high,
        "pullback_pct": pullback_pct,
        "stage": stage,
        "hint": _STAGE_HINTS[stage],
    }


def prior_swing_low(
    closes: list[float], window: int = 30, swing_span: int = 3
) -> dict[str, float | int] | None:
    """找近 window 天高點之前，最近一次的起漲低點（前後 swing_span 天都不比它低）。

    這是判斷「跌破重要前低」紀律用的參考點：先定位近 window 天高點，
    再往回找高點之前最近一個局部低點，也就是這波上漲行情大概是從哪裡起漲的。
    找不到符合條件的低點（例如整段區間一路上漲、沒有明顯回檔）時回傳 None。
    """
    if len(closes) < window:
        return None
    segment = closes[-window:]
    high_offset = segment.index(max(segment))
    high_idx = len(closes) - window + high_offset
    for i in range(high_idx - 1, swing_span - 1, -1):
        left = closes[i - swing_span : i]
        right = closes[i + 1 : i + 1 + swing_span]
        if len(left) < swing_span or len(right) < swing_span:
            continue
        if closes[i] <= min(left) and closes[i] <= min(right):
            return {"price": closes[i], "days_before_high": high_idx - i}
    return None


def trend_still_intact(
    close: float, ma20: float | None, prior_low_price: float | None
) -> bool | None:
    """4~6% 加碼區間用：收盤價是否還在 MA20 之上，且還沒跌破起漲前低。

    兩個條件缺一都算「趨勢沒壞」不成立；任一個輸入資料不足（None）時，
    代表無法判斷，回傳 None 而不是猜一個布林值。
    """
    if ma20 is None or prior_low_price is None:
        return None
    return close > ma20 and close > prior_low_price


def momentum_stabilizing(
    closes: list[float], rsi_window: int = 14, lookback: int = 3
) -> bool | None:
    """6~9% 加碼區間用：RSI 比 lookback 天前回升，且今天收盤沒創近 lookback 天新低。"""
    if len(closes) <= lookback:
        return None
    rsi_series = relative_strength_index(closes, rsi_window)
    if len(rsi_series) <= lookback:
        return None
    current_rsi, past_rsi = rsi_series[-1], rsi_series[-1 - lookback]
    if current_rsi is None or past_rsi is None:
        return None
    rsi_recovering = current_rsi > past_rsi
    no_new_low = closes[-1] >= min(closes[-1 - lookback : -1])
    return rsi_recovering and no_new_low


def macd_golden_cross(closes: list[float], lookback: int = 3) -> bool | None:
    """>9% 最後一批用：近 lookback 天內 MACD histogram 是否由負轉正，且目前仍是正值。"""
    result = macd(closes)
    histogram = result["histogram"]
    if len(histogram) <= lookback:
        return None
    current = histogram[-1]
    recent = histogram[-1 - lookback : -1]
    if current is None or any(h is None for h in recent):
        return None
    return current > 0 and any(h <= 0 for h in recent)


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def technical_score(
    close: float,
    ma20: float | None,
    rsi14: float | None,
    bollinger: dict[str, float | None],
    macd_value: float | None,
) -> dict | None:
    """把 MA20/RSI14/布林/MACD 換算成 0~100 冷熱分數。

    分數越高代表技術面越偏冷卻（區間低檔），分數越低代表越偏熱（區間高檔）。
    任一指標缺值就跳過那一項，四項（或更少）平均得出總分；沒有任何指標可用時
    回傳 None。這不是自動買賣訊號，只是把既有指標換算成同一個尺度方便比較。
    """
    scores: dict[str, float] = {}
    if rsi14 is not None:
        scores["rsi14"] = _clamp(100 - rsi14)
    upper, lower = bollinger.get("upper"), bollinger.get("lower")
    if upper is not None and lower is not None and upper != lower:
        position_pct = (close - lower) / (upper - lower) * 100
        scores["bollinger"] = _clamp(100 - position_pct)
    if ma20 is not None and ma20 != 0:
        deviation_pct = (close - ma20) / ma20 * 100
        scores["ma20"] = _clamp(50 - deviation_pct * 5)
    if macd_value is not None and close != 0:
        macd_pct = (macd_value / close) * 100
        scores["macd"] = _clamp(50 - macd_pct * 10)
    if not scores:
        return None
    composite = sum(scores.values()) / len(scores)
    if composite >= 65:
        label = "技術面偏冷"
    elif composite >= 35:
        label = "技術面中性"
    else:
        label = "技術面偏熱"
    return {"scores": scores, "composite": composite, "label": label}


def synthetic_price_series(
    base_by_date: dict[str, float], multiplier_by_date: dict[str, float]
) -> list[float]:
    """依共同日期逐日相乘兩個序列（例如美元金價 x 美元兌台幣），依日期排序回傳。"""
    common_dates = sorted(set(base_by_date) & set(multiplier_by_date))
    return [base_by_date[d] * multiplier_by_date[d] for d in common_dates]


def divergence_flag(
    gold_by_date: dict[str, float], fx_by_date: dict[str, float], window: int = 5
) -> dict[str, int | bool]:
    """比對金價與美元最近 window 天的漲跌方向；正常應多為反向，同向天數 >=3 視為背離旗標。"""
    common_dates = sorted(set(gold_by_date) & set(fx_by_date))
    recent_dates = common_dates[-(window + 1) :]
    same_direction_days = 0
    checked_days = 0
    for i in range(1, len(recent_dates)):
        prev_date, curr_date = recent_dates[i - 1], recent_dates[i]
        gold_change = gold_by_date[curr_date] - gold_by_date[prev_date]
        fx_change = fx_by_date[curr_date] - fx_by_date[prev_date]
        if gold_change == 0 or fx_change == 0:
            continue
        checked_days += 1
        if (gold_change > 0) == (fx_change > 0):
            same_direction_days += 1
    return {
        "same_direction_days": same_direction_days,
        "checked_days": checked_days,
        "is_divergent": same_direction_days >= 3,
    }
