"""從 Yahoo Finance 公開 chart API 抓總體背景參考序列（國際金價、USD/TWD）。

伺服器端 Python 直接呼叫，不會碰到瀏覽器端才有的 CORS 限制。
抓取失敗時退回本地快取，快取也沒有才真的拋出例外——讓呼叫端（build_site.py）
可以清楚知道「這次真的完全拿不到資料」還是「用了舊資料」。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import requests
from loguru import logger

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


@dataclass(frozen=True)
class PricePoint:
    date: str  # YYYY-MM-DD
    close: float


@dataclass(frozen=True)
class MacroSeries:
    symbol: str
    points: list[PricePoint]
    as_of: str
    stale: bool  # True 代表這次即時抓取失敗，改用快取資料


def fetch_series(symbol: str, cache_path: Path, range_: str = "6mo") -> MacroSeries:
    """抓 `symbol`（例如 'GC=F'、'USDTWD=X'）的每日收盤序列。

    即時抓取失敗時退回 `cache_path` 的快取；連快取都沒有才真的拋出例外。
    """
    try:
        points = _fetch_from_yahoo(symbol, range_)
    except (requests.RequestException, ValueError, KeyError) as exc:
        logger.warning("{} 即時抓取失敗：{}，改用本地快取", symbol, exc)
        cached = _read_cache(cache_path)
        if cached is None:
            raise
        return MacroSeries(
            symbol=symbol, points=cached["points"], as_of=cached["as_of"], stale=True
        )

    as_of = points[-1].date if points else "unknown"
    series = MacroSeries(symbol=symbol, points=points, as_of=as_of, stale=False)
    _write_cache(cache_path, series)
    return series


def _fetch_from_yahoo(symbol: str, range_: str) -> list[PricePoint]:
    response = requests.get(
        CHART_URL.format(symbol=symbol),
        params={"interval": "1d", "range": range_},
        timeout=10,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    payload = response.json()
    result = payload["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    points: list[PricePoint] = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        points.append(PricePoint(date=_timestamp_to_date(ts), close=float(close)))
    if not points:
        raise ValueError(f"{symbol}: Yahoo Finance returned no usable price points")
    return points


def _timestamp_to_date(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d")


def _read_cache(cache_path: Path) -> dict | None:
    if not cache_path.exists():
        return None
    raw = json.loads(cache_path.read_text(encoding="utf-8"))
    return {
        "points": [PricePoint(**p) for p in raw["points"]],
        "as_of": raw["as_of"],
    }


def _write_cache(cache_path: Path, series: MacroSeries) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"as_of": series.as_of, "points": [asdict(p) for p in series.points]}
    cache_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
