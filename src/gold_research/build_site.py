"""組裝 gold_site.json 並產生黃金相關研究站的靜態頁面。"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from gold_research.candidates import CANDIDATES
from gold_research.fetch_macro import MacroSeries, fetch_series
from gold_research.fetch_twse import DailyBar, fetch_month
from gold_research.indicators import (
    bollinger_bands,
    macd,
    relative_strength_index,
    simple_moving_average,
)

DISCLAIMER = "商品／避險資產研究參考，不構成投資建議，不自動下單，不保證收益。"
ASSET_CLASS_NOTE = (
    "商品期貨型ETF，追蹤COMEX黃金期貨，受美元、實質利率、避險情緒、通膨預期影響，"
    "不是公司基本面驅動；MA/RSI/布林/MACD 是通用技術工具，解讀方式跟股票型ETF不同。"
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SITE_DIR = PROJECT_ROOT / "site"
DATA_CACHE_DIR = PROJECT_ROOT / "data_cache"


def build_payload(
    bars: list[DailyBar],
    macro_gold: MacroSeries,
    macro_fx: MacroSeries,
    generated_at: datetime,
) -> dict:
    closes = [bar.close for bar in bars]
    sma20 = simple_moving_average(closes, 20)
    rsi14 = relative_strength_index(closes, 14)
    bands = bollinger_bands(closes, 20, 2.0)
    macd_result = macd(closes, 12, 26, 9)
    latest_bar = bars[-1]

    return {
        "generated_at": generated_at.isoformat(),
        "disclaimer": DISCLAIMER,
        "targets": {
            "00635U": {
                "code": "00635U",
                "display_name": "元大S&P黃金",
                "asset_class": "commodity_futures_etf",
                "asset_class_note": ASSET_CLASS_NOTE,
                "data_source": {
                    "name": "TWSE STOCK_DAY",
                    "as_of": latest_bar.trading_date,
                },
                "latest": {
                    "trading_date": latest_bar.trading_date,
                    "close": latest_bar.close,
                    "volume": latest_bar.volume,
                },
                "indicators": {
                    "ma20": sma20[-1],
                    "rsi14": rsi14[-1],
                    "bollinger": {
                        "upper": bands["upper"][-1],
                        "middle": bands["middle"][-1],
                        "lower": bands["lower"][-1],
                    },
                    "macd": {
                        "macd": macd_result["macd"][-1],
                        "signal": macd_result["signal"][-1],
                        "histogram": macd_result["histogram"][-1],
                    },
                },
                "chart": [
                    {"trading_date": bar.trading_date, "close": bar.close}
                    for bar in bars
                ],
            }
        },
        "macro_context": {
            "data_source": {"name": "Yahoo Finance", "as_of": macro_gold.as_of},
            "comex_gold_usd": [asdict(p) for p in macro_gold.points],
            "usdtwd": [asdict(p) for p in macro_fx.points],
            "stale": macro_gold.stale or macro_fx.stale,
            "note": "總體背景參考，非本頁技術指標，僅供市場情緒判讀。",
        },
        "candidates": [
            {
                "code": c.code,
                "name": c.name,
                "type": c.type_,
                "note": c.note,
                "source": asdict(c.source),
            }
            for c in CANDIDATES
        ],
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8" />
<title>黃金相關研究</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
  body { font-family: -apple-system, "Noto Sans TC", sans-serif; margin: 0; padding: 24px; background: #0f1115; color: #e6e6e6; }
  .disclaimer { background: #3a2b00; border: 1px solid #a97c00; padding: 12px 16px; border-radius: 8px; margin-bottom: 20px; }
  .card { background: #1a1d24; border-radius: 10px; padding: 16px 20px; margin-bottom: 16px; }
  .badge { display: inline-block; background: #52370f; color: #ffcf7a; padding: 2px 10px; border-radius: 999px; font-size: 12px; margin-left: 8px; }
  .indicator-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-top: 12px; }
  .indicator { background: #12141a; padding: 10px 12px; border-radius: 8px; }
  .source-note { color: #9aa0a6; font-size: 12px; margin-top: 8px; }
  .candidate { border-left: 3px solid #52370f; padding-left: 12px; margin-bottom: 12px; }
  a { color: #7ab8ff; }
</style>
</head>
<body>
  <h1>黃金相關研究</h1>
  <div class="disclaimer" id="disclaimer"></div>
  <div class="card" id="target-card"></div>
  <div class="card" id="macro-card"></div>
  <div class="card" id="candidates-card"></div>
<script>
const SITE_DATA = __DATA__;

function el(tag, props, children) {
  const node = document.createElement(tag);
  Object.assign(node, props || {});
  (children || []).forEach((child) => node.appendChild(child));
  return node;
}

function renderDisclaimer() {
  document.getElementById("disclaimer").textContent = SITE_DATA.disclaimer;
}

function renderTarget() {
  const target = SITE_DATA.targets["00635U"];
  const card = document.getElementById("target-card");
  card.innerHTML = "";
  const heading = el("h2", { textContent: target.display_name + " (" + target.code + ") " });
  heading.appendChild(el("span", { className: "badge", textContent: "商品／避險資產，非股票型ETF" }));
  card.appendChild(heading);
  card.appendChild(el("p", { textContent: target.asset_class_note }));
  card.appendChild(el("p", { textContent: "最新收盤：" + target.latest.close + "（" + target.latest.trading_date + "）" }));
  const grid = el("div", { className: "indicator-grid" });
  grid.appendChild(el("div", { className: "indicator", textContent: "MA20：" + target.indicators.ma20 }));
  grid.appendChild(el("div", { className: "indicator", textContent: "RSI14：" + target.indicators.rsi14 }));
  grid.appendChild(el("div", { className: "indicator", textContent: "布林通道：" + target.indicators.bollinger.lower + " ~ " + target.indicators.bollinger.upper }));
  grid.appendChild(el("div", { className: "indicator", textContent: "MACD：" + target.indicators.macd.macd + " / 訊號線 " + target.indicators.macd.signal }));
  card.appendChild(grid);
  card.appendChild(el("p", { className: "source-note", textContent: "資料來源：" + target.data_source.name + "，最後更新 " + target.data_source.as_of }));
}

function renderMacro() {
  const macro = SITE_DATA.macro_context;
  const card = document.getElementById("macro-card");
  card.innerHTML = "";
  card.appendChild(el("h2", { textContent: "總體背景參考" }));
  card.appendChild(el("p", { textContent: macro.note }));
  if (macro.stale) {
    card.appendChild(el("p", { className: "source-note", textContent: "⚠ 資料暫時無法取得，顯示上次成功抓取的結果。" }));
  }
  card.appendChild(el("p", { className: "source-note", textContent: "資料來源：" + macro.data_source.name + "，最後更新 " + macro.data_source.as_of }));
}

function renderCandidates() {
  const card = document.getElementById("candidates-card");
  card.innerHTML = "";
  card.appendChild(el("h2", { textContent: "候選標的清單（研究參考，未接自動化技術面）" }));
  SITE_DATA.candidates.forEach((c) => {
    const item = el("div", { className: "candidate" });
    item.appendChild(el("h3", { textContent: c.name + "（" + c.code + "）· " + c.type }));
    item.appendChild(el("p", { textContent: c.note }));
    const link = el("a", { href: c.source.url, textContent: c.source.name, target: "_blank" });
    const sourceLine = el("p", { className: "source-note" });
    sourceLine.appendChild(document.createTextNode("資料來源："));
    sourceLine.appendChild(link);
    sourceLine.appendChild(document.createTextNode("，最後更新 " + c.source.as_of));
    item.appendChild(sourceLine);
    card.appendChild(item);
  });
}

renderDisclaimer();
renderTarget();
renderMacro();
renderCandidates();
</script>
</body>
</html>
"""


def render_html(payload: dict) -> str:
    data_json = json.dumps(payload, ensure_ascii=False)
    return HTML_TEMPLATE.replace("__DATA__", data_json)


def main() -> None:
    now = datetime.now(UTC).astimezone()
    year, month = now.year, now.month
    bars = fetch_month("00635U", year, month)
    if len(bars) < 30:
        prev_month = month - 1 or 12
        prev_year = year if month > 1 else year - 1
        bars = fetch_month("00635U", prev_year, prev_month) + bars

    macro_gold = fetch_series("GC=F", DATA_CACHE_DIR / "gc_f.json")
    macro_fx = fetch_series("USDTWD=X", DATA_CACHE_DIR / "usdtwd.json")

    payload = build_payload(bars, macro_gold, macro_fx, now)

    (SITE_DIR / "data").mkdir(parents=True, exist_ok=True)
    (SITE_DIR / "data" / "gold_site.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (SITE_DIR / "index.html").write_text(render_html(payload), encoding="utf-8")


if __name__ == "__main__":
    main()
