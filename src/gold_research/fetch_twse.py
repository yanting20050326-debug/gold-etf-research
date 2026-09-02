"""從 TWSE 公開的端點抓取單一標的的日線 OHLCV，以及盤中即時報價。

日線用官方的 STOCK_DAY 端點；即時報價用 mis.twse.com.tw 的看盤資訊端點
（未公開文件、非正式 API，欄位可能無預警變動，所以抓取失敗一律回傳 None，
不讓整個網站產生流程中斷——這也是為什麼即時報價跟日線分成兩個函式：
即時報價只用來顯示「現在價格」，技術指標仍然只用日線資料計算，兩者互不影響。
這裡用來抓 00635U（元大S&P黃金）等台灣掛牌代碼，兩個端點都對任何 TWSE 代碼通用。
"""

from __future__ import annotations

from dataclasses import dataclass

import requests
from loguru import logger

STOCK_DAY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
REALTIME_QUOTE_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"


@dataclass(frozen=True)
class DailyBar:
    trading_date: str  # ISO 格式 YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class RealtimeQuote:
    code: str
    price: float
    previous_close: float
    quote_date: str  # YYYYMMDD（TWSE 原始格式，非西元 ISO）
    quote_time: str  # HH:MM:SS


class TwseFetchError(Exception):
    """TWSE 端點回傳非預期格式時拋出。"""


def fetch_month(stock_no: str, year: int, month: int) -> list[DailyBar]:
    """抓 `stock_no`（例如 '00635U'）在 `year`-`month` 這個月份的日線資料。"""
    query_date = f"{year:04d}{month:02d}01"
    response = requests.get(
        STOCK_DAY_URL,
        params={"response": "json", "date": query_date, "stockNo": stock_no},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("stat") != "OK":
        raise TwseFetchError(f"TWSE STOCK_DAY 回傳 stat={payload.get('stat')!r}")

    bars: list[DailyBar] = []
    for row in payload.get("data", []):
        try:
            bars.append(_parse_row(row))
        except (ValueError, IndexError, TypeError) as exc:
            logger.warning("跳過無法解析的 TWSE 資料列 {}：{}", row, exc)
    return bars


def _parse_row(row: list[str]) -> DailyBar:
    roc_date, volume_str, _amount, open_str, high_str, low_str, close_str, *_rest = row
    return DailyBar(
        trading_date=_roc_to_iso(roc_date),
        open=_to_float(open_str),
        high=_to_float(high_str),
        low=_to_float(low_str),
        close=_to_float(close_str),
        volume=int(volume_str.replace(",", "")),
    )


def _to_float(value: str) -> float:
    return float(value.replace(",", ""))


def _roc_to_iso(roc_date: str) -> str:
    year_str, month_str, day_str = roc_date.split("/")
    return f"{int(year_str) + 1911:04d}-{int(month_str):02d}-{int(day_str):02d}"


def _best_book_price(book_str: str | None) -> float | None:
    """從 TWSE 的五檔報價字串（用底線分隔，第一個是最佳價）取出最佳價。"""
    if not book_str:
        return None
    first = book_str.split("_")[0]
    if not first or first == "-":
        return None
    try:
        return float(first)
    except ValueError:
        return None


def fetch_realtime_quote(stock_no: str) -> RealtimeQuote | None:
    """抓 `stock_no` 的盤中即時報價；抓不到、格式異常、或非交易時段一律回傳 None。

    非官方端點，刻意不拋例外——呼叫端（build_site.py）在 None 時應該
    直接沿用當日收盤價，而不是讓整個網站產生流程中斷。
    """
    try:
        response = requests.get(
            REALTIME_QUOTE_URL,
            params={"ex_ch": f"tse_{stock_no}.tw", "json": "1", "delay": "0"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("rtmessage") != "OK":
            logger.warning(
                "{} 即時報價回傳非 OK：{}", stock_no, payload.get("rtmessage")
            )
            return None
        rows = payload.get("msgArray") or []
        if not rows:
            return None
        row = rows[0]
        previous_close = float(row["y"])
        # TWSE 用字面上的 "-" 代表「今天還沒有成交價」，不是空字串；"-" 在
        # Python 是真值，`row.get("z") or row.get("y")` 這種寫法不會真的
        # fallback。有些冷門標的（例如 00635U）整天都不會填 z，即使買賣
        # 報價明明是活的，這時候退回昨收價會顯示一個完全不會動、跟現在
        # 市場脫節的舊數字；改用買一買二的中間價當現價，比昨收價準得多。
        z_value = row.get("z")
        if z_value and z_value != "-":
            price = float(z_value)
        else:
            best_ask = _best_book_price(row.get("a"))
            best_bid = _best_book_price(row.get("b"))
            if best_ask is not None and best_bid is not None:
                price = (best_ask + best_bid) / 2
            else:
                y_value = row.get("y")
                if not y_value or y_value == "-":
                    return None
                price = float(y_value)
        return RealtimeQuote(
            code=row.get("c", stock_no),
            price=price,
            previous_close=previous_close,
            quote_date=row.get("d", ""),
            quote_time=row.get("t", ""),
        )
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        logger.warning("{} 即時報價抓取失敗：{}", stock_no, exc)
        return None
