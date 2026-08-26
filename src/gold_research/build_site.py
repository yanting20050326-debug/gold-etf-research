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
    divergence_flag,
    macd,
    relative_position,
    relative_strength_index,
    simple_moving_average,
    volatility_squeeze,
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
    vol_squeeze = volatility_squeeze(closes, 5, 20)
    rel_position = relative_position(closes, 30)
    gold_by_date = {p.date: p.close for p in macro_gold.points}
    fx_by_date = {p.date: p.close for p in macro_fx.points}
    divergence = divergence_flag(gold_by_date, fx_by_date, 5)
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
                    "volatility_squeeze": vol_squeeze,
                    "relative_position": rel_position,
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
            "divergence": divergence,
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
  :root {
    --bg: #f6f8fb;
    --card-bg: #ffffff;
    --stat-bg: #fbfdff;
    --border: #d9e2ef;
    --text: #111827;
    --text-secondary: #334155;
    --text-muted: #64748b;
    --accent: #2563eb;
    --accent-shadow: rgba(37, 99, 235, 0.35);
    --card-shadow: rgba(15, 23, 42, 0.08);
    --gold: #b45309;
    --gold-bg: #fff7e6;
    --gold-border: #f0c869;
  }
  * { box-sizing: border-box; }
  body {
    font-family: "Microsoft JhengHei", "Noto Sans TC", system-ui, sans-serif;
    margin: 0;
    padding: 32px 16px 48px;
    background: var(--bg);
    color: var(--text);
  }
  .shell { max-width: 880px; margin: 0 auto; }
  h1 { font-size: 32px; font-weight: 700; margin: 0 0 16px; }
  h2 { font-size: 20px; font-weight: 700; margin: 0 0 8px; }
  h3 { font-size: 16px; font-weight: 700; margin: 0 0 4px; }
  .disclaimer { background: var(--gold-bg); border: 1px solid var(--gold-border); color: #7a4a00; padding: 12px 16px; border-radius: 8px; margin-bottom: 20px; font-size: 14px; }
  .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 16px; box-shadow: 0 18px 50px var(--card-shadow); }
  .badge { display: inline-block; background: var(--gold-bg); color: var(--gold); border: 1px solid var(--gold-border); padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; margin-left: 8px; vertical-align: middle; }
  .badge-warn { background: #fee2e2; color: #b91c1c; border-color: #fca5a5; }
  .badge-ok { background: #e0f2e9; color: #15803d; border-color: #86efac; }
  .indicator-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-top: 12px; }
  .indicator { background: var(--stat-bg); border: 1px solid var(--border); padding: 12px 14px; border-radius: 8px; cursor: pointer; }
  .indicator-summary { font-weight: 700; }
  .indicator-detail { margin-top: 8px; font-size: 13px; color: var(--text-secondary); line-height: 1.5; }
  .tab-bar { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
  .tab-button { background: var(--card-bg); color: var(--text-secondary); border: 1px solid var(--border); border-radius: 8px; padding: 10px 16px; cursor: pointer; font-size: 15px; font-weight: 700; font-family: inherit; }
  .tab-button.active { background: var(--accent); color: #ffffff; border-color: var(--accent); box-shadow: 0 8px 20px var(--accent-shadow); }
  .source-note { color: var(--text-muted); font-size: 12px; margin-top: 8px; }
  .candidate { border-left: 3px solid var(--gold-border); padding-left: 14px; margin-bottom: 16px; }
  a { color: var(--accent); }
</style>
</head>
<body>
  <div class="shell">
  <h1>黃金相關研究</h1>
  <div class="disclaimer" id="disclaimer"></div>
  <div class="tab-bar" id="target-tabs"></div>
  <div class="card" id="target-card"></div>
  <div class="card" id="macro-card"></div>
  <div class="card" id="candidates-card"></div>
  </div>
<script>
const SITE_DATA = __DATA__;

const INDICATOR_EXPLANATIONS = {
  ma20: "MA20 是近 20 個交易日的平均收盤價，用來看價格的中期趨勢方向。黃金的均線走勢常跟著美元指數、實質利率反向擺動，不是公司獲利成長那種趨勢。",
  rsi14: "RSI14 衡量近期漲跌力道，超過 70 通常視為短線過熱、低於 30 視為超賣。黃金的 RSI 過熱經常是避險情緒推動（例如地緣政治或股災），不必然代表基本面轉弱，過熱不代表一定要賣。",
  bollinger: "布林通道用近 20 日的平均價 ± 2 倍標準差，顯示目前價格相對波動區間的位置。黃金的波動區間常因美元走勢、央行政策會議而突然放大或收斂。",
  macd: "MACD 比較短期與長期均線的差距，搭配訊號線判斷動能轉折。黃金的動能轉折常跟利率預期（例如聯準會會議）同步發生，本身沒有財報或產業循環可以對照。",
  volatility_squeeze: "比較近 5 日和近 20 日的價格波動度。比值明顯偏低代表最近盤整、波動被壓縮，過去這種情況常是變盤（不管往上或往下）前兆，不代表方向。",
  relative_position: "顯示今天收盤價在近 30 天最高最低區間中的相對位置。低點區不代表馬上反彈、高點區也不代表馬上回檔，只是提供「現在算便宜還是貴」的參考座標。",
};

function fmt(value, digits) {
  if (value === null || value === undefined) return "資料不足";
  return value.toFixed(digits);
}

function el(tag, props, children) {
  const node = document.createElement(tag);
  Object.assign(node, props || {});
  (children || []).forEach((child) => node.appendChild(child));
  return node;
}

function renderDisclaimer() {
  document.getElementById("disclaimer").textContent = SITE_DATA.disclaimer;
}

function sparklinePath(points, width, height, padding) {
  if (points.length === 0) return "";
  const values = points.map((p) => p.close);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const stepX = (width - padding * 2) / Math.max(points.length - 1, 1);
  return values
    .map((v, i) => {
      const x = padding + i * stepX;
      const y = padding + (height - padding * 2) * (1 - (v - min) / range);
      return (i === 0 ? "M" : "L") + x.toFixed(1) + "," + y.toFixed(1);
    })
    .join(" ");
}

function renderSparkline(points, color) {
  const width = 320;
  const height = 80;
  const padding = 6;
  const path = sparklinePath(points, width, height, padding);
  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", "0 0 " + width + " " + height);
  svg.setAttribute("width", "100%");
  svg.setAttribute("height", height);
  const pathEl = document.createElementNS(svgNS, "path");
  pathEl.setAttribute("d", path);
  pathEl.setAttribute("fill", "none");
  pathEl.setAttribute("stroke", color);
  pathEl.setAttribute("stroke-width", "2");
  svg.appendChild(pathEl);
  return svg;
}

function indicatorCard(key, label, valueText) {
  const card = el("div", { className: "indicator", tabIndex: 0 });
  const summary = el("div", { className: "indicator-summary", textContent: label + "：" + valueText });
  const detail = el("div", { className: "indicator-detail", textContent: INDICATOR_EXPLANATIONS[key] });
  detail.style.display = "none";
  card.appendChild(summary);
  card.appendChild(detail);
  card.addEventListener("click", () => {
    detail.style.display = detail.style.display === "none" ? "block" : "none";
  });
  return card;
}

let currentTargetCode = null;

function renderTargetTabs() {
  const codes = Object.keys(SITE_DATA.targets);
  const tabBar = document.getElementById("target-tabs");
  tabBar.innerHTML = "";
  codes.forEach((code) => {
    const label = SITE_DATA.targets[code].display_name + "（" + code + "）";
    const btn = el("button", { className: "tab-button", textContent: label });
    btn.dataset.code = code;
    btn.addEventListener("click", () => {
      renderTarget(code);
    });
    tabBar.appendChild(btn);
  });
}

function updateTabActiveState() {
  document.querySelectorAll("#target-tabs .tab-button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.code === currentTargetCode);
  });
}

function renderTarget(code) {
  currentTargetCode = code;
  const target = SITE_DATA.targets[code];
  const card = document.getElementById("target-card");
  card.innerHTML = "";
  const heading = el("h2", { textContent: target.display_name + " (" + target.code + ") " });
  heading.appendChild(el("span", { className: "badge", textContent: "商品／避險資產，非股票型ETF" }));
  card.appendChild(heading);
  card.appendChild(el("p", { textContent: target.asset_class_note }));
  card.appendChild(el("p", { textContent: "最新收盤：" + fmt(target.latest.close, 2) + "（" + target.latest.trading_date + "）" }));
  const grid = el("div", { className: "indicator-grid" });
  grid.appendChild(indicatorCard("ma20", "MA20", fmt(target.indicators.ma20, 2)));
  grid.appendChild(indicatorCard("rsi14", "RSI14", fmt(target.indicators.rsi14, 2)));
  grid.appendChild(indicatorCard("bollinger", "布林通道", fmt(target.indicators.bollinger.lower, 2) + " ~ " + fmt(target.indicators.bollinger.upper, 2)));
  grid.appendChild(indicatorCard("macd", "MACD", fmt(target.indicators.macd.macd, 4) + " / 訊號線 " + fmt(target.indicators.macd.signal, 4)));
  const vs = target.indicators.volatility_squeeze;
  const vsText = vs.ratio === null ? "資料不足" : fmt(vs.ratio, 2) + (vs.is_compressed ? "（壓縮中）" : "");
  grid.appendChild(indicatorCard("volatility_squeeze", "波動壓縮比", vsText));
  const rp = target.indicators.relative_position;
  const rpText = rp === null ? "資料不足" : fmt(rp.position * 100, 0) + "%（" + rp.label + "）";
  grid.appendChild(indicatorCard("relative_position", "區間位置", rpText));
  card.appendChild(grid);
  if (target.chart && target.chart.length > 1) {
    card.appendChild(el("p", { textContent: "近 " + target.chart.length + " 個交易日走勢：" }));
    card.appendChild(renderSparkline(target.chart, "#b45309"));
  }
  card.appendChild(el("p", { className: "source-note", textContent: "資料來源：" + target.data_source.name + "，最後更新 " + target.data_source.as_of }));
  updateTabActiveState();
}

function renderMacro() {
  const macro = SITE_DATA.macro_context;
  const card = document.getElementById("macro-card");
  card.innerHTML = "";
  card.appendChild(el("h2", { textContent: "總體背景參考" }));
  card.appendChild(el("p", { textContent: macro.note }));
  card.appendChild(el("p", { textContent: "背景脈絡：美元走弱、實質利率走低、避險情緒升溫，通常對金價有利；但這只是歷史上常見的關聯性，不是保證，也不是進出場訊號。" }));
  if (macro.stale) {
    card.appendChild(el("p", { className: "source-note", textContent: "⚠ 資料暫時無法取得，顯示上次成功抓取的結果。" }));
  }
  const goldPoints = macro.comex_gold_usd;
  const fxPoints = macro.usdtwd;
  if (goldPoints.length > 0) {
    const latestGold = goldPoints[goldPoints.length - 1];
    card.appendChild(el("p", { textContent: "國際金價（COMEX 黃金期貨）：$" + fmt(latestGold.close, 2) + " 美元（" + latestGold.date + "）" }));
    if (goldPoints.length > 1) card.appendChild(renderSparkline(goldPoints, "#eab308"));
  }
  if (fxPoints.length > 0) {
    const latestFx = fxPoints[fxPoints.length - 1];
    card.appendChild(el("p", { textContent: "USD/TWD：" + fmt(latestFx.close, 3) + "（" + latestFx.date + "）" }));
    if (fxPoints.length > 1) card.appendChild(renderSparkline(fxPoints, "#2563eb"));
  }
  const div = macro.divergence;
  if (div && div.checked_days > 0) {
    const divLabel = div.is_divergent ? "⚠ 背離：近期同向天數偏多" : "正常反向關係";
    const divBadgeClass = div.is_divergent ? "badge badge-warn" : "badge badge-ok";
    const divP = el("p", {});
    divP.appendChild(document.createTextNode("金價與美元關係："));
    divP.appendChild(el("span", { className: divBadgeClass, textContent: divLabel }));
    card.appendChild(divP);
    card.appendChild(el("p", { className: "source-note", textContent: "近 " + div.checked_days + " 個有效交易日中，有 " + div.same_direction_days + " 天金價與美元同向變動（正常應多為反向）。" }));
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
renderTargetTabs();
renderTarget(Object.keys(SITE_DATA.targets)[0]);
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

    if not bars:
        raise RuntimeError("00635U: no TWSE bars fetched for the requested months")

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
