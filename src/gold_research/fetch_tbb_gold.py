"""臺灣企銀黃金存摺牌價（網路銀行公開頁面，不需登入）。

這不是官方 API，是把銀行公開網頁的內容解析出來，所以頁面改版就可能壞掉。
因此解析時一律先驗證版面（欄位順序、必要欄位），對不上就丟 TbbGoldFetchError，
讓呼叫端沿用上次發布的資料，而不是把錯位的數字當成牌價顯示出來。

名詞：
- 「本行賣出價格」＝客戶向銀行**買進**黃金的價格（比較高），本站回檔階段以它為準。
- 「本行買進價格」＝客戶賣回給銀行的價格（比較低）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import requests

BASE_URL = "https://ebank.tbb.com.tw/nb3/GOLD/OUT/PASSBOOK"
DAY_PRICE_URL = f"{BASE_URL}/day_price_query"
HISTORY_FORM_URL = f"{BASE_URL}/history_price_query"
HISTORY_RESULT_URL = f"{BASE_URL}/history_price_query_result"
USER_AGENT = "Mozilla/5.0"
TIMEOUT_SECONDS = 20
DEFAULT_HISTORY_RANGE = "LASTSIXMON"  # 最近半年（約 125 個營業日）


class TbbGoldFetchError(Exception):
    """抓取失敗，或頁面版面跟預期不同（可能改版）。"""


@dataclass(frozen=True)
class PassbookQuote:
    """當日最新一次掛牌的牌價。"""

    sell: float  # 本行賣出價格（你買進）
    buy: float  # 本行買進價格（你賣出）
    listed_date: str  # YYYY-MM-DD
    listed_time: str  # HH:MM:SS


@dataclass(frozen=True)
class PassbookDay:
    """歷史牌價的一個營業日。"""

    trading_date: str  # YYYY-MM-DD
    buy: float
    sell: float


def _to_price(text: str) -> float:
    return float(text.replace(",", "").strip())


def parse_current_quote(html: str) -> PassbookQuote | None:
    """解析當日牌價頁。

    回傳 None 代表頁面正常但今天沒有牌價（例如假日）；頁面結構不對則丟錯。
    """
    match = re.search(r"var data = (\{.*?\});\s*var holiday_flag", html, re.DOTALL)
    if not match:
        raise TbbGoldFetchError("當日牌價頁找不到內嵌資料，頁面可能已改版")
    try:
        payload = json.loads(match.group(1))
        records = payload["data"]["REC"]["data"]
    except (ValueError, KeyError, TypeError) as exc:
        raise TbbGoldFetchError(f"當日牌價資料格式不符：{exc}") from exc
    if not records:
        return None
    record = records[0]
    try:
        raw_date = record["LASTDATE"]  # 20261117
        raw_time = record["LASTTIME"]  # 101008
        return PassbookQuote(
            sell=_to_price(record["BPRICE"]),
            buy=_to_price(record["SPRICE"]),
            listed_date=f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}",
            listed_time=f"{raw_time[0:2]}:{raw_time[2:4]}:{raw_time[4:6]}",
        )
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        raise TbbGoldFetchError(f"當日牌價欄位無法解析：{exc}") from exc


_HEADER_RE = re.compile(r"<th[^>]*>(.*?)</th>", re.DOTALL)
_ROW_RE = re.compile(
    r"<tr>\s*<td[^>]*>(\d{4}/\d{2}/\d{2})</td>\s*"
    r"<td[^>]*>([\d,]+)</td>\s*<td[^>]*>([\d,]+)</td>",
    re.DOTALL,
)


def parse_history(html: str) -> list[PassbookDay]:
    """解析歷史牌價表，依日期由舊到新排序。

    先檢查表頭順序是「日期、買進、賣出」，避免銀行調換欄位後把買賣價搞反。
    """
    headers = [re.sub(r"<[^>]*>|\s+", "", h) for h in _HEADER_RE.findall(html)]
    try:
        buy_index = headers.index("本行買進價格")
        sell_index = headers.index("本行賣出價格")
    except ValueError as exc:
        raise TbbGoldFetchError(
            "歷史牌價表頭找不到買進／賣出欄位，頁面可能已改版"
        ) from exc
    if not buy_index < sell_index:
        raise TbbGoldFetchError("歷史牌價欄位順序改變（賣出在買進之前），拒絕解析")
    days = [
        PassbookDay(
            trading_date=raw_date.replace("/", "-"),
            buy=_to_price(buy),
            sell=_to_price(sell),
        )
        for raw_date, buy, sell in _ROW_RE.findall(html)
    ]
    if not days:
        raise TbbGoldFetchError("歷史牌價表沒有任何資料列")
    days.sort(key=lambda d: d.trading_date)
    return days


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def fetch_current_quote() -> PassbookQuote | None:
    try:
        response = _session().get(DAY_PRICE_URL, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise TbbGoldFetchError(f"當日牌價抓取失敗：{exc}") from exc
    return parse_current_quote(response.text)


def fetch_history(query_type: str = DEFAULT_HISTORY_RANGE) -> list[PassbookDay]:
    """抓歷史牌價。query_type：LASTDAY / LASTMON / LASTSIXMON / LASTYEAR。"""
    session = _session()
    try:
        # 先 GET 查詢頁取得 session cookie，再 POST 查詢結果。
        session.get(HISTORY_FORM_URL, timeout=TIMEOUT_SECONDS).raise_for_status()
        response = session.post(
            HISTORY_RESULT_URL,
            data={"QUERYTYPE": query_type},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise TbbGoldFetchError(f"歷史牌價抓取失敗：{exc}") from exc
    return parse_history(response.text)
