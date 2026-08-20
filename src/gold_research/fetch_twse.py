"""從 TWSE 公開的 STOCK_DAY 端點抓取單一標的的日線 OHLCV。

這裡用來抓 00635U（元大S&P黃金），但這個端點對任何 TWSE 掛牌代碼都通用。
"""

from __future__ import annotations

from dataclasses import dataclass

import requests
from loguru import logger

STOCK_DAY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"


@dataclass(frozen=True)
class DailyBar:
    trading_date: str  # ISO 格式 YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: int


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
    for row in payload["data"]:
        try:
            bars.append(_parse_row(row))
        except (ValueError, IndexError) as exc:
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
