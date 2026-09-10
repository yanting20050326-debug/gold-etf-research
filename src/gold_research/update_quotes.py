"""輕量級的「只抓報價」腳本，跟完整的 build_site.py 分開跑。

技術指標（MA/RSI/MACD 等）用的是每日收盤價，報價再怎麼即時也不會影響
指標計算結果，所以沒必要為了更新報價數字就重跑一次完整的建站流程
（月線抓取、日線序列、AI 摘要）。這支腳本只做兩件事：抓 TWSE 即時報價
跟 Yahoo 分鐘線報價，寫進一個小的 live_quotes.json，可以用比完整建站
快很多的頻率執行（例如每分鐘），不用擔心浪費資源重算用不到的東西。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from gold_research.build_site import (
    INTL_GOLD_CODE,
    SITE_DIR,
    TARGET_META,
    should_fetch_twse,
)
from gold_research.fetch_macro import LatestQuote, fetch_latest_price
from gold_research.fetch_twse import RealtimeQuote, fetch_realtime_quote


def build_live_quotes_payload(
    twse_realtime: dict[str, RealtimeQuote | None],
    intl_gold_realtime: LatestQuote | None,
    generated_at: datetime,
) -> dict:
    quotes: dict[str, dict] = {}
    for code, realtime in twse_realtime.items():
        if realtime is not None:
            quotes[code] = {
                "close": realtime.price,
                "quote_time": realtime.quote_time,
                "is_realtime": True,
            }
    if intl_gold_realtime is not None:
        quotes[INTL_GOLD_CODE] = {
            "close": intl_gold_realtime.price,
            "quote_time": intl_gold_realtime.quote_time,
            "is_realtime": True,
        }
    return {"generated_at": generated_at.isoformat(), "quotes": quotes}


def main() -> None:
    generated_at = datetime.now(UTC).astimezone()
    twse_realtime = (
        {code: fetch_realtime_quote(code) for code in TARGET_META}
        if should_fetch_twse(generated_at)
        else {}
    )
    intl_gold_realtime = fetch_latest_price("GC=F")
    payload = build_live_quotes_payload(twse_realtime, intl_gold_realtime, generated_at)

    data_dir: Path = SITE_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "live_quotes.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
