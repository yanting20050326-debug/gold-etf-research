"""組裝 gold_site.json 並產生黃金相關研究站的靜態頁面。"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from gold_research.fetch_macro import (
    LatestQuote,
    MacroSeries,
    PricePoint,
    fetch_latest_price,
    fetch_series,
)
from gold_research.fetch_twse import (
    DailyBar,
    RealtimeQuote,
    fetch_month,
    fetch_realtime_quote,
)
from gold_research.indicators import (
    bollinger_bands,
    divergence_flag,
    macd,
    pullback_stage,
    relative_position,
    relative_strength_index,
    simple_moving_average,
    technical_score,
    volatility_squeeze,
)

DISCLAIMER = "商品／避險資產研究參考，不構成投資建議，不自動下單，不保證收益。"

TARGET_META = {
    "00635U": {
        "display_name": "元大S&P黃金",
        "asset_class": "commodity_futures_etf",
        "asset_class_note": (
            "商品期貨型ETF，追蹤COMEX黃金期貨，受美元、實質利率、避險情緒、通膨預期影響，"
            "不是公司基本面驅動；MA/RSI/布林/MACD 是通用技術工具，解讀方式跟股票型ETF不同。"
        ),
        "badge": "商品／避險資產，非股票型ETF",
    },
    "00708L": {
        "display_name": "期元大S&P黃金正2",
        "asset_class": "leveraged_futures_etf",
        "asset_class_note": (
            "槓桿期貨型ETF（2倍），追蹤COMEX黃金期貨單日報酬的2倍，僅適合短線操作；"
            "長期持有會因複利效應偏離原型指數，不建議當作定期定額標的。"
        ),
        "badge": "2倍槓桿，僅適合短線",
    },
}

INTL_GOLD_CODE = "XAUUSD"
INTL_GOLD_META = {
    "display_name": "國際盤黃金（COMEX）",
    "asset_class": "commodity_spot",
    "asset_class_note": (
        "國際現貨／期貨黃金報價（美元計價），是台灣黃金相關標的（00635U、00708L、"
        "銀行黃金存摺）的共同定價基礎；本身無法在台灣交易所直接買賣。"
    ),
    "badge": "國際報價，非台股標的",
}

GOLD_PASSBOOK_NOTE = (
    "台灣銀行／兆豐銀行等黃金存摺沒有公開歷史報價 API，本站無法直接串接技術指標。"
    "存摺價格通常貼著國際金價換算新台幣走，可用這裡的區間位置，"
    "作為存摺現在算貴還是便宜的參考方向；實際牌價仍受銀行買賣價差影響，並非完全同步，"
    "請以銀行公告牌價為準。"
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SITE_DIR = PROJECT_ROOT / "site"
DATA_CACHE_DIR = PROJECT_ROOT / "data_cache"


def _compute_indicators(closes: list[float]) -> dict:
    sma20 = simple_moving_average(closes, 20)
    rsi14 = relative_strength_index(closes, 14)
    bands = bollinger_bands(closes, 20, 2.0)
    macd_result = macd(closes, 12, 26, 9)
    return {
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
        "volatility_squeeze": volatility_squeeze(closes, 5, 20),
        "relative_position": relative_position(closes, 30),
        "pullback_stage": pullback_stage(closes, 30),
        "technical_score": technical_score(
            close=closes[-1],
            ma20=sma20[-1],
            rsi14=rsi14[-1],
            bollinger={"upper": bands["upper"][-1], "lower": bands["lower"][-1]},
            macd_value=macd_result["macd"][-1],
        ),
    }


def _build_latest(
    trading_date: str, close: float, volume: int | None, realtime: object | None
) -> dict:
    """組裝 `latest` 欄位；有即時報價就用即時報價覆蓋顯示，沒有就用日線收盤。"""
    if realtime is None:
        return {
            "trading_date": trading_date,
            "close": close,
            "volume": volume,
            "quote_time": None,
            "is_realtime": False,
        }
    if isinstance(realtime, RealtimeQuote):
        return {
            "trading_date": trading_date,
            "close": realtime.price,
            "volume": volume,
            "quote_time": realtime.quote_time,
            "is_realtime": True,
        }
    if isinstance(realtime, LatestQuote):
        return {
            "trading_date": trading_date,
            "close": realtime.price,
            "volume": volume,
            "quote_time": realtime.quote_time,
            "is_realtime": True,
        }
    raise TypeError(f"unsupported realtime quote type: {type(realtime)!r}")


def _build_twse_target(
    code: str, bars: list[DailyBar], realtime: RealtimeQuote | None = None
) -> dict:
    meta = TARGET_META[code]
    closes = [bar.close for bar in bars]
    latest_bar = bars[-1]
    return {
        "code": code,
        "display_name": meta["display_name"],
        "asset_class": meta["asset_class"],
        "asset_class_note": meta["asset_class_note"],
        "badge": meta["badge"],
        "data_source": {"name": "TWSE STOCK_DAY", "as_of": latest_bar.trading_date},
        "latest": _build_latest(
            latest_bar.trading_date, latest_bar.close, latest_bar.volume, realtime
        ),
        "indicators": _compute_indicators(closes),
        "chart": [
            {"trading_date": bar.trading_date, "close": bar.close} for bar in bars
        ],
    }


def _build_intl_gold_target(
    points: list[PricePoint], as_of: str, realtime: LatestQuote | None = None
) -> dict:
    closes = [p.close for p in points]
    latest_point = points[-1]
    return {
        "code": INTL_GOLD_CODE,
        "display_name": INTL_GOLD_META["display_name"],
        "asset_class": INTL_GOLD_META["asset_class"],
        "asset_class_note": INTL_GOLD_META["asset_class_note"],
        "badge": INTL_GOLD_META["badge"],
        "data_source": {"name": "Yahoo Finance (GC=F)", "as_of": as_of},
        "latest": _build_latest(latest_point.date, latest_point.close, None, realtime),
        "indicators": _compute_indicators(closes),
        "chart": [{"trading_date": p.date, "close": p.close} for p in points],
        "passbook_note": GOLD_PASSBOOK_NOTE,
    }


def build_payload(
    twse_bars: dict[str, list[DailyBar]],
    macro_gold: MacroSeries,
    macro_fx: MacroSeries,
    generated_at: datetime,
    twse_realtime: dict[str, RealtimeQuote | None] | None = None,
    intl_gold_realtime: LatestQuote | None = None,
) -> dict:
    twse_realtime = twse_realtime or {}
    targets = {
        code: _build_twse_target(code, bars, twse_realtime.get(code))
        for code, bars in twse_bars.items()
    }
    if macro_gold.points:
        targets[INTL_GOLD_CODE] = _build_intl_gold_target(
            macro_gold.points, macro_gold.as_of, intl_gold_realtime
        )

    gold_by_date = {p.date: p.close for p in macro_gold.points}
    fx_by_date = {p.date: p.close for p in macro_fx.points}
    divergence = divergence_flag(gold_by_date, fx_by_date, 5)

    return {
        "generated_at": generated_at.isoformat(),
        "disclaimer": DISCLAIMER,
        "auto_refresh_seconds": 60,
        "targets": targets,
        "macro_context": {
            "data_source": {"name": "Yahoo Finance", "as_of": macro_fx.as_of},
            "usdtwd": [asdict(p) for p in macro_fx.points],
            "stale": macro_gold.stale or macro_fx.stale,
            "note": "總體背景參考，非本頁技術指標，僅供市場情緒判讀。",
            "divergence": divergence,
        },
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
  .gauge-wrap { margin-top: 10px; }
  .gauge-track { position: relative; height: 8px; background: linear-gradient(90deg, #86efac, #fde68a, #fca5a5); border-radius: 999px; }
  .gauge-marker { position: absolute; top: -4px; width: 4px; height: 16px; background: var(--text); border-radius: 2px; transform: translateX(-2px); }
  .gauge-labels { display: flex; justify-content: space-between; font-size: 11px; color: var(--text-muted); margin-top: 4px; }
  .score-card { background: var(--stat-bg); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; margin: 12px 0; }
  .score-head { display: flex; justify-content: space-between; align-items: baseline; }
  .score-label { font-weight: 700; font-size: 15px; }
  .score-composite { font-weight: 700; font-size: 28px; color: var(--accent); }
  .score-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(90px, 1fr)); gap: 8px; margin-top: 10px; }
  .score-item { background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; padding: 8px; text-align: center; }
  .score-item-label { display: block; font-size: 11px; color: var(--text-muted); }
  .score-item-value { display: block; font-weight: 700; font-size: 18px; margin-top: 2px; }
  .live-dot { display: inline-block; width: 8px; height: 8px; border-radius: 999px; background: #22c55e; margin-right: 6px; vertical-align: middle; }
  .discipline-card { background: var(--gold-bg); border: 1px solid var(--gold-border); border-radius: 8px; padding: 14px 16px; margin: 12px 0; }
  .discipline-list { margin: 8px 0 0; padding-left: 20px; font-size: 13px; line-height: 1.6; color: var(--text-secondary); }
  .buy-hint { background: #eff6ff; border: 1px solid #93c5fd; border-radius: 8px; padding: 10px 14px; margin-bottom: 10px; }
  .buy-hint-text { font-weight: 700; color: #1e40af; margin: 0; }
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

const DISCIPLINE_RULES = [
  "只做中期多頭的黃金。",
  "不在暴漲創高時追價。",
  "回檔 3～4% 開始第一筆 40%。",
  "回檔 5～6%，趨勢沒壞再加 20%。",
  "6～9% 區間出現止跌，再加 20%。",
  "最後 20% 一定等重新轉強。",
  "跌破重要前低，立即停止加碼。",
  "不要無限攤平。",
  "+5%、+8%、+10～12% 分批獲利。",
  "剩餘 40% 讓趨勢決定出場。",
];

function renderDisciplineCard(target) {
  const wrap = el("div", { className: "discipline-card" });
  wrap.appendChild(el("h3", { textContent: "核心交易紀律（個人參考，非自動訊號）" }));
  const ps = target.indicators.pullback_stage;
  if (ps) {
    const hintBox = el("div", { className: "buy-hint" });
    hintBox.appendChild(el("p", { className: "buy-hint-text", textContent: "提示：" + ps.hint }));
    wrap.appendChild(hintBox);
    wrap.appendChild(el("p", {
      textContent: "近 30 天高點 " + fmt(ps.recent_high, 2) + "，目前回檔 " + fmt(ps.pullback_pct, 1) + "%，對照紀律大概落在：" + ps.stage,
    }));
  } else {
    wrap.appendChild(el("p", { className: "source-note", textContent: "資料不足，無法計算目前回檔幅度。" }));
  }
  const list = el("ul", { className: "discipline-list" });
  DISCIPLINE_RULES.forEach((rule) => {
    list.appendChild(el("li", { textContent: rule }));
  });
  wrap.appendChild(list);
  wrap.appendChild(el("p", { className: "source-note", textContent: "回檔基準為近 30 天高點；趨勢是否轉強、止跌、跌破前低仍需自行判斷，本卡不做自動買賣訊號。獲利分批（+5%/+8%/+10~12%）需要你自己記錄進場價才能對照，本站不追蹤持倉。" }));
  return wrap;
}

function renderPositionGauge(rp) {
  if (!rp) return null;
  const wrap = el("div", { className: "gauge-wrap" });
  const track = el("div", { className: "gauge-track" });
  const marker = el("div", { className: "gauge-marker" });
  const pct = Math.max(0, Math.min(100, rp.position * 100));
  marker.style.left = pct.toFixed(1) + "%";
  track.appendChild(marker);
  wrap.appendChild(track);
  const labels = el("div", { className: "gauge-labels" });
  labels.appendChild(el("span", { textContent: "低點 0%" }));
  labels.appendChild(el("span", { textContent: "高點 100%" }));
  wrap.appendChild(labels);
  return wrap;
}

const SCORE_ITEM_LABELS = { rsi14: "RSI14", bollinger: "布林通道", ma20: "MA20", macd: "MACD" };

function renderTechnicalScore(target) {
  const ts = target.indicators.technical_score;
  if (!ts) return null;
  const wrap = el("div", { className: "score-card" });
  const headRow = el("div", { className: "score-head" });
  headRow.appendChild(el("span", { className: "score-label", textContent: ts.label }));
  headRow.appendChild(el("span", { className: "score-composite", textContent: fmt(ts.composite, 0) + " 分" }));
  wrap.appendChild(headRow);
  wrap.appendChild(el("p", { className: "source-note", textContent: "分數越高代表技術面越偏冷卻、分數越低代表越偏熱；由 RSI／布林／MA20／MACD 平均得出，僅供研究參考，不是進出場訊號。" }));
  const grid = el("div", { className: "score-grid" });
  Object.keys(ts.scores).forEach((key) => {
    const item = el("div", { className: "score-item" });
    item.appendChild(el("span", { className: "score-item-label", textContent: SCORE_ITEM_LABELS[key] || key }));
    item.appendChild(el("span", { className: "score-item-value", textContent: fmt(ts.scores[key], 0) }));
    grid.appendChild(item);
  });
  wrap.appendChild(grid);
  return wrap;
}

function indicatorCard(key, label, valueText, extraNode) {
  const card = el("div", { className: "indicator", tabIndex: 0 });
  const summary = el("div", { className: "indicator-summary", textContent: label + "：" + valueText });
  card.appendChild(summary);
  if (extraNode) card.appendChild(extraNode);
  const detail = el("div", { className: "indicator-detail", textContent: INDICATOR_EXPLANATIONS[key] });
  detail.style.display = "none";
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

let currentHorizon = "short";

function renderHorizonTabs(card) {
  const bar = el("div", { className: "tab-bar horizon-tab-bar" });
  const options = [
    { key: "short", label: "短線" },
    { key: "long", label: "長線" },
  ];
  options.forEach((opt) => {
    const btn = el("button", { className: "tab-button", textContent: opt.label });
    btn.dataset.horizon = opt.key;
    if (opt.key === currentHorizon) btn.classList.add("active");
    btn.addEventListener("click", () => {
      currentHorizon = opt.key;
      renderTarget(currentTargetCode);
      updateMacroVisibility();
    });
    bar.appendChild(btn);
  });
  card.appendChild(bar);
}

function updateMacroVisibility() {
  const macroCard = document.getElementById("macro-card");
  macroCard.style.display = currentHorizon === "long" ? "" : "none";
}

function renderTarget(code) {
  currentTargetCode = code;
  const target = SITE_DATA.targets[code];
  const card = document.getElementById("target-card");
  card.innerHTML = "";
  const heading = el("h2", { textContent: target.display_name + " (" + target.code + ") " });
  heading.appendChild(el("span", { className: "badge", textContent: target.badge }));
  card.appendChild(heading);
  card.appendChild(el("p", { textContent: target.asset_class_note }));
  const priceLine = el("p", {});
  if (target.latest.is_realtime) {
    const dot = el("span", { className: "live-dot" });
    priceLine.appendChild(dot);
    priceLine.appendChild(document.createTextNode("現在：" + fmt(target.latest.close, 2) + "（" + target.latest.quote_time + " 盤中，非官方公告價）"));
  } else {
    priceLine.appendChild(document.createTextNode("最新收盤：" + fmt(target.latest.close, 2) + "（" + target.latest.trading_date + "，非即時）"));
  }
  card.appendChild(priceLine);
  if (target.passbook_note) {
    card.appendChild(el("p", { className: "source-note", textContent: target.passbook_note }));
  }
  const scoreNode = renderTechnicalScore(target);
  if (scoreNode) card.appendChild(scoreNode);
  renderHorizonTabs(card);
  card.appendChild(el("p", { className: "source-note", textContent: currentHorizon === "short" ? "短線：技術面擇時指標（RSI／布林通道／波動壓縮／區間位置）" : "長線：趨勢與總體面（MA／MACD，下方另有匯率、背離旗標）" }));
  card.appendChild(renderDisciplineCard(target));
  const grid = el("div", { className: "indicator-grid" });
  if (currentHorizon === "short") {
    grid.appendChild(indicatorCard("rsi14", "RSI14", fmt(target.indicators.rsi14, 2)));
    grid.appendChild(indicatorCard("bollinger", "布林通道", fmt(target.indicators.bollinger.lower, 2) + " ~ " + fmt(target.indicators.bollinger.upper, 2)));
    const vs = target.indicators.volatility_squeeze;
    const vsText = vs.ratio === null ? "資料不足" : fmt(vs.ratio, 2) + (vs.is_compressed ? "（壓縮中）" : "");
    grid.appendChild(indicatorCard("volatility_squeeze", "波動壓縮比", vsText));
    const rp = target.indicators.relative_position;
    const rpText = rp === null ? "資料不足" : fmt(rp.position * 100, 0) + "%（" + rp.label + "）";
    grid.appendChild(indicatorCard("relative_position", "區間位置", rpText, renderPositionGauge(rp)));
  } else {
    grid.appendChild(indicatorCard("ma20", "MA20", fmt(target.indicators.ma20, 2)));
    grid.appendChild(indicatorCard("macd", "MACD", fmt(target.indicators.macd.macd, 4) + " / 訊號線 " + fmt(target.indicators.macd.signal, 4)));
  }
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
  const fxPoints = macro.usdtwd;
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

function refreshSiteData() {
  fetch("data/gold_site.json?ts=" + Date.now())
    .then((res) => (res.ok ? res.json() : null))
    .then((fresh) => {
      if (!fresh) return;
      Object.assign(SITE_DATA, fresh);
      renderDisclaimer();
      renderTargetTabs();
      renderTarget(currentTargetCode || Object.keys(SITE_DATA.targets)[0]);
      renderMacro();
      updateMacroVisibility();
    })
    .catch(() => {});
}

renderDisclaimer();
renderTargetTabs();
renderTarget(Object.keys(SITE_DATA.targets)[0]);
renderMacro();
updateMacroVisibility();
if (SITE_DATA.auto_refresh_seconds) {
  setInterval(refreshSiteData, SITE_DATA.auto_refresh_seconds * 1000);
}
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

    twse_bars: dict[str, list[DailyBar]] = {}
    for code in TARGET_META:
        bars = fetch_month(code, year, month)
        if len(bars) < 30:
            prev_month = month - 1 or 12
            prev_year = year if month > 1 else year - 1
            bars = fetch_month(code, prev_year, prev_month) + bars
        if not bars:
            raise RuntimeError(f"{code}: no TWSE bars fetched for the requested months")
        twse_bars[code] = bars

    macro_gold = fetch_series("GC=F", DATA_CACHE_DIR / "gc_f.json")
    macro_fx = fetch_series("USDTWD=X", DATA_CACHE_DIR / "usdtwd.json")

    twse_realtime = {code: fetch_realtime_quote(code) for code in TARGET_META}
    intl_gold_realtime = fetch_latest_price("GC=F")

    payload = build_payload(
        twse_bars, macro_gold, macro_fx, now, twse_realtime, intl_gold_realtime
    )

    (SITE_DIR / "data").mkdir(parents=True, exist_ok=True)
    (SITE_DIR / "data" / "gold_site.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (SITE_DIR / "index.html").write_text(render_html(payload), encoding="utf-8")


if __name__ == "__main__":
    main()
