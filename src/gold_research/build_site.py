"""組裝 gold_site.json 並產生黃金相關研究站的靜態頁面。"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

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
    TwseFetchError,
    fetch_month,
    fetch_realtime_quote,
)
from gold_research.indicators import (
    bollinger_bands,
    divergence_flag,
    macd,
    macd_golden_cross,
    momentum_stabilizing,
    prior_swing_low,
    pullback_stage,
    relative_position,
    relative_strength_index,
    simple_moving_average,
    synthetic_price_series,
    technical_score,
    trend_still_intact,
    volatility_squeeze,
)
from gold_research.llm_summary import AI_SUMMARY_DISCLAIMER, generate_daily_summary
from gold_research.notify_line import check_and_notify, write_notify_state

load_dotenv()

DISCLAIMER = "商品／避險資產研究參考，不構成投資建議，不自動下單，不保證收益。"

TARGET_META = {
    "00635U": {
        "display_name": "元大S&P黃金",
        "asset_class": "commodity_futures_etf",
        "asset_class_note": (
            "商品期貨型ETF，追蹤COMEX黃金期貨，受美元、實質利率、避險情緒、通膨預期影響，"
            "不是公司基本面驅動；沒有槓桿，長期持有不會有槓桿耗損，但期貨轉倉遇到正價差時"
            "會有小幅轉倉成本，長期報酬可能略遜於現貨金價；MA/RSI/布林/MACD 是通用技術工具，"
            "解讀方式跟股票型ETF不同。"
        ),
        "badge": "商品／避險資產，非股票型ETF",
        "pullback_scale": 1.0,
    },
    "00708L": {
        "display_name": "期元大S&P黃金正2",
        "asset_class": "leveraged_futures_etf",
        "asset_class_note": (
            "槓桿期貨型ETF（2倍），追蹤COMEX黃金期貨單日報酬的2倍，僅適合短線操作；"
            "長期持有會因複利效應偏離原型指數，不建議當作定期定額標的。"
        ),
        "badge": "2倍槓桿，僅適合短線",
        "pullback_scale": 2.0,
    },
}

INTL_GOLD_CODE = "GCF"
INTL_GOLD_META = {
    "display_name": "國際盤黃金（COMEX）",
    "asset_class": "commodity_futures",
    "asset_class_note": (
        "追蹤COMEX黃金期貨（Yahoo Finance代碼GC=F），不是銀行間即期黃金報價（真正的"
        "XAUUSD）；兩者走勢高度連動、價差通常很小，但不是同一個報價來源。是台灣黃金"
        "相關標的（00635U、00708L、銀行黃金存摺）的共同定價基礎，本身無法在台灣交易所"
        "直接買賣。"
    ),
    "badge": "COMEX期貨報價，非台股標的",
}

GOLD_PASSBOOK_NOTE = (
    "台灣銀行黃金存摺沒有公開歷史報價 API，這裡改用「國際金價 x 美元兌台幣」"
    "換算成新台幣／公克的試算價格，估算存摺可能的相對位置與回檔階段；"
    "實際牌價仍受銀行買賣價差、換匯時點影響，並非完全同步，請以銀行公告牌價為準。"
)

TROY_OUNCE_GRAMS = 31.1034768

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SITE_DIR = PROJECT_ROOT / "site"
DATA_CACHE_DIR = PROJECT_ROOT / "data_cache"
STATIC_DIR = PROJECT_ROOT / "static"

# GitHub Actions 排程每次都在全新、跑完就丟棄的機器上執行，本機檔案不會保留到
# 下一次執行；LINE 通知需要記得「上次通知過的階段」才能只在真的變化時發送，
# 所以直接讀回已公開的 notify_state.json 當作狀態來源，寫入時一併放進
# site/ 目錄，讓它跟著這次建置結果一起發布到公開 repo，下次執行再讀回來。
PUBLISHED_NOTIFY_STATE_URL = (
    "https://yanting20050326-debug.github.io/gold-research-site/notify_state.json"
)
PUBLISHED_SITE_DATA_URL = (
    "https://yanting20050326-debug.github.io/gold-research-site/data/gold_site.json"
)

# TWSE 只有交易日盤中才有新資料——STOCK_DAY 日線一天只變一次、即時報價收盤後
# 完全是死的，非交易時間仍每 5 分鐘照打只是白白增加撞到 502 的機會。視窗刻意
# 比實際盤中（09:00-13:30）寬一點，含開盤前熱身跟收盤後資料入帳的緩衝；國定
# 假日沒有另外排除（不維護假日曆），該天照樣會在視窗內打，STOCK_DAY 遇到休市
# 一樣能正常查到最近一個交易日的資料，不影響正確性，只是多打幾次。
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
TWSE_SESSION_START = time(8, 30)
TWSE_SESSION_END = time(14, 30)


def _compute_indicators(closes: list[float], pullback_scale: float = 1.0) -> dict:
    sma20 = simple_moving_average(closes, 20)
    rsi14 = relative_strength_index(closes, 14)
    bands = bollinger_bands(closes, 20, 2.0)
    macd_result = macd(closes, 12, 26, 9)
    prior_low = prior_swing_low(closes, 30)
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
        "pullback_stage": pullback_stage(closes, 30, threshold_scale=pullback_scale),
        "prior_low": prior_low,
        "technical_score": technical_score(
            close=closes[-1],
            ma20=sma20[-1],
            rsi14=rsi14[-1],
            bollinger={"upper": bands["upper"][-1], "lower": bands["lower"][-1]},
            macd_value=macd_result["macd"][-1],
        ),
        "stage_signals": {
            "trend_intact": trend_still_intact(
                closes[-1], sma20[-1], prior_low["price"] if prior_low else None
            ),
            "momentum_stabilizing": momentum_stabilizing(closes),
            "macd_golden_cross": macd_golden_cross(closes),
        },
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


def _pct_change(prev: float | None, curr: float | None) -> float | None:
    if prev is None or curr is None or prev == 0:
        return None
    return (curr - prev) / prev * 100


def _ai_summary_field(ai_summary) -> dict | None:
    if ai_summary is None:
        return None
    return {"text": ai_summary.text, "disclaimer": AI_SUMMARY_DISCLAIMER}


def _build_twse_target(
    code: str,
    bars: list[DailyBar],
    realtime: RealtimeQuote | None,
    intl_gold_change_pct: float | None,
    usdtwd_change_pct: float | None,
    today: date,
    cache_dir: Path,
) -> dict:
    meta = TARGET_META[code]
    closes = [bar.close for bar in bars]
    latest_bar = bars[-1]
    pullback_scale = meta.get("pullback_scale", 1.0)
    indicators = _compute_indicators(closes, pullback_scale=pullback_scale)
    own_change_pct = _pct_change(
        bars[-2].close if len(bars) >= 2 else None, latest_bar.close
    )
    technical_label = (
        indicators["technical_score"]["label"]
        if indicators["technical_score"]
        else None
    )
    ai_summary = generate_daily_summary(
        meta["display_name"],
        own_change_pct,
        intl_gold_change_pct,
        usdtwd_change_pct,
        technical_label,
        cache_dir / f"ai_summary_{code}.json",
        today,
    )
    return {
        "code": code,
        "display_name": meta["display_name"],
        "asset_class": meta["asset_class"],
        "asset_class_note": meta["asset_class_note"],
        "badge": meta["badge"],
        "pullback_scale": pullback_scale,
        "data_source": {"name": "TWSE STOCK_DAY", "as_of": latest_bar.trading_date},
        "latest": _build_latest(
            latest_bar.trading_date, latest_bar.close, latest_bar.volume, realtime
        ),
        "indicators": indicators,
        "chart": [
            {"trading_date": bar.trading_date, "close": bar.close} for bar in bars
        ],
        "ai_summary": _ai_summary_field(ai_summary),
    }


def _build_passbook_estimate(
    gold_points: list[PricePoint], fx_points: list[PricePoint]
) -> dict | None:
    """把國際金價（美元／盎司）換算成新台幣／公克序列，估算存摺可能的相對位置。"""
    gold_by_date = {p.date: p.close for p in gold_points}
    fx_by_date = {p.date: p.close for p in fx_points}
    usd_per_ounce = synthetic_price_series(gold_by_date, fx_by_date)
    if not usd_per_ounce:
        return None
    ntd_per_gram = [price / TROY_OUNCE_GRAMS for price in usd_per_ounce]
    indicators = _compute_indicators(ntd_per_gram)
    return {
        "unit": "NT$/公克（試算）",
        "latest_price": ntd_per_gram[-1],
        "relative_position": indicators["relative_position"],
        "pullback_stage": indicators["pullback_stage"],
        "technical_score": indicators["technical_score"],
        "note": GOLD_PASSBOOK_NOTE,
    }


def _build_intl_gold_target(
    points: list[PricePoint],
    as_of: str,
    realtime: LatestQuote | None,
    usdtwd_change_pct: float | None,
    fx_points: list[PricePoint],
    today: date,
    cache_dir: Path,
) -> dict:
    closes = [p.close for p in points]
    latest_point = points[-1]
    indicators = _compute_indicators(closes)
    own_change_pct = _pct_change(
        points[-2].close if len(points) >= 2 else None, latest_point.close
    )
    technical_label = (
        indicators["technical_score"]["label"]
        if indicators["technical_score"]
        else None
    )
    ai_summary = generate_daily_summary(
        INTL_GOLD_META["display_name"],
        own_change_pct,
        own_change_pct,  # 國際盤黃金自己就是「國際金價」，兩者相同
        usdtwd_change_pct,
        technical_label,
        cache_dir / f"ai_summary_{INTL_GOLD_CODE}.json",
        today,
    )
    return {
        "code": INTL_GOLD_CODE,
        "display_name": INTL_GOLD_META["display_name"],
        "asset_class": INTL_GOLD_META["asset_class"],
        "asset_class_note": INTL_GOLD_META["asset_class_note"],
        "badge": INTL_GOLD_META["badge"],
        "pullback_scale": 1.0,
        "quote_delay_note": (
            "Yahoo Finance 免費期貨資料通常延遲約 10 分鐘，"
            "此標的的即時價格不會比這個延遲更即時，跟 TWSE 標的的近乎即時報價不同。"
        ),
        "data_source": {"name": "Yahoo Finance (GC=F)", "as_of": as_of},
        "latest": _build_latest(latest_point.date, latest_point.close, None, realtime),
        "indicators": indicators,
        "chart": [{"trading_date": p.date, "close": p.close} for p in points],
        "passbook": _build_passbook_estimate(points, fx_points),
        "ai_summary": _ai_summary_field(ai_summary),
    }


def build_payload(
    twse_bars: dict[str, list[DailyBar]],
    macro_gold: MacroSeries,
    macro_fx: MacroSeries,
    generated_at: datetime,
    twse_realtime: dict[str, RealtimeQuote | None] | None = None,
    intl_gold_realtime: LatestQuote | None = None,
    cache_dir: Path = DATA_CACHE_DIR,
) -> dict:
    twse_realtime = twse_realtime or {}
    today = generated_at.date()
    intl_gold_change_pct = _pct_change(
        macro_gold.points[-2].close if len(macro_gold.points) >= 2 else None,
        macro_gold.points[-1].close if macro_gold.points else None,
    )
    usdtwd_change_pct = _pct_change(
        macro_fx.points[-2].close if len(macro_fx.points) >= 2 else None,
        macro_fx.points[-1].close if macro_fx.points else None,
    )
    targets = {
        code: _build_twse_target(
            code,
            bars,
            twse_realtime.get(code),
            intl_gold_change_pct,
            usdtwd_change_pct,
            today,
            cache_dir,
        )
        for code, bars in twse_bars.items()
    }
    if macro_gold.points:
        targets[INTL_GOLD_CODE] = _build_intl_gold_target(
            macro_gold.points,
            macro_gold.as_of,
            intl_gold_realtime,
            usdtwd_change_pct,
            macro_fx.points,
            today,
            cache_dir,
        )

    gold_by_date = {p.date: p.close for p in macro_gold.points}
    fx_by_date = {p.date: p.close for p in macro_fx.points}
    divergence = divergence_flag(gold_by_date, fx_by_date, 5)

    return {
        "generated_at": generated_at.isoformat(),
        "disclaimer": DISCLAIMER,
        "auto_refresh_seconds": 60,
        "live_refresh_seconds": 15,
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
  .indicator-signal { margin-top: 6px; font-size: 12px; color: var(--text-secondary); }
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
  .ai-summary-card { background: #f5f3ff; border: 1px solid #ddd6fe; border-radius: 8px; padding: 14px 16px; margin: 12px 0; }
  .discipline-card { background: var(--gold-bg); border: 1px solid var(--gold-border); border-radius: 8px; padding: 14px 16px; margin: 12px 0; }
  .discipline-list { margin: 8px 0 0; padding-left: 20px; font-size: 13px; line-height: 1.6; color: var(--text-secondary); }
  .buy-hint { background: #eff6ff; border: 1px solid #93c5fd; border-radius: 8px; padding: 10px 14px; margin-bottom: 10px; }
  .buy-hint-text { font-weight: 700; color: #1e40af; margin: 0; }
  .passbook-card { background: #ecfdf5; border: 1px solid #a7f3d0; border-radius: 8px; padding: 14px 16px; margin: 12px 0; }
  a { color: var(--accent); }
</style>
</head>
<body>
  <div class="shell">
  <p class="source-note"><a href="index.html">← 封面</a></p>
  <h1>黃金相關研究</h1>
  <div class="disclaimer" id="disclaimer"></div>
  <p class="source-note" id="page-updated-line">頁面最後更新：<span id="page-updated-time">--:--:--</span></p>
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

function formatClock(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return pad(date.getHours()) + ":" + pad(date.getMinutes()) + ":" + pad(date.getSeconds());
}

function markPageUpdatedNow() {
  const el = document.getElementById("page-updated-time");
  if (el) el.textContent = formatClock(new Date());
}

function latestCheckedDate() {
  const candidates = [SITE_DATA.generated_at, SITE_DATA.live_quotes_generated_at]
    .filter(Boolean)
    .map((iso) => new Date(iso))
    .filter((d) => !isNaN(d.getTime()));
  if (candidates.length === 0) return null;
  return new Date(Math.max(...candidates.map((d) => d.getTime())));
}

function formatBuildCheckedTime() {
  const date = latestCheckedDate();
  return date ? formatClock(date) : null;
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

function disciplineRules(scale) {
  const t1 = 3 * scale, t2 = 4 * scale, t3 = 6 * scale, t4 = 9 * scale;
  return [
    "只做中期多頭的黃金。",
    "不在暴漲創高時追價。",
    "回檔 " + t1 + "～" + t2 + "% 開始第一筆 20%。",
    "回檔 " + t2 + "～" + t3 + "%，趨勢沒壞（收盤價在 MA20 之上且未跌破起漲前低）再加 20%。",
    t3 + "～" + t4 + "% 區間出現止跌（RSI14 比 3 天前回升且未創近 3 天新低），再加 20%。",
    "最後 40% 一定等重新轉強（MACD 出現黃金交叉）。",
    "跌破重要前低（起漲低點），立即停止加碼。",
    "不要無限攤平。",
    "+5%、+8%、+10～12% 分批獲利。",
    "剩餘 40% 讓趨勢決定出場。",
  ];
}

function stageSignalInfo(stage, signals) {
  if (!signals) return null;
  if (stage === "加碼 20%（前提：趨勢沒壞）") {
    return { label: "趨勢是否還沒壞", value: signals.trend_intact };
  }
  if (stage === "加碼 20%（前提：出現止跌）") {
    return { label: "是否已經止跌", value: signals.momentum_stabilizing };
  }
  if (stage === "最後 40%（等重新轉強）") {
    return { label: "是否已經重新轉強（MACD 黃金交叉）", value: signals.macd_golden_cross };
  }
  return null;
}

function signalBadgeParagraph(label, value) {
  const badgeText = value === true ? "✓ 符合" : value === false ? "✗ 尚未符合" : "資料不足";
  const badgeClass = value === true ? "badge badge-ok" : value === false ? "badge badge-warn" : "badge";
  const p = el("p", { className: "indicator-signal" });
  p.appendChild(document.createTextNode(label + "："));
  p.appendChild(el("span", { className: badgeClass, textContent: badgeText }));
  return p;
}

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
    const signalInfo = stageSignalInfo(ps.stage, target.indicators.stage_signals);
    if (signalInfo) {
      wrap.appendChild(signalBadgeParagraph(signalInfo.label, signalInfo.value));
    }
  } else {
    wrap.appendChild(el("p", { className: "source-note", textContent: "資料不足，無法計算目前回檔幅度。" }));
  }
  const pl = target.indicators.prior_low;
  if (pl) {
    wrap.appendChild(el("p", {
      textContent: "起漲前低（" + pl.days_before_high + " 天前）：" + fmt(pl.price, 2) + "，跌破這裡代表這波漲勢可能已經轉弱。",
    }));
  } else {
    wrap.appendChild(el("p", { className: "source-note", textContent: "資料不足，暫時抓不到明確的起漲前低。" }));
  }
  const scale = target.pullback_scale || 1;
  if (scale !== 1) {
    wrap.appendChild(el("p", { className: "source-note", textContent: "此標的為 " + scale + " 倍槓桿，以下回檔門檻已按 " + scale + " 倍幅度計算，跟現貨標的不是同一組數字。" }));
  }
  const list = el("ul", { className: "discipline-list" });
  disciplineRules(scale).forEach((rule) => {
    list.appendChild(el("li", { textContent: rule }));
  });
  wrap.appendChild(list);
  wrap.appendChild(el("p", { className: "source-note", textContent: "回檔基準為近 30 天高點；趨勢是否轉強、止跌、跌破前低仍需自行判斷，本卡不做自動買賣訊號。獲利分批（+5%/+8%/+10~12%）需要你自己記錄進場價才能對照，本站不追蹤持倉。" }));
  return wrap;
}

function renderPassbookEstimate(target) {
  const pb = target.passbook;
  if (!pb) return null;
  const wrap = el("div", { className: "passbook-card" });
  wrap.appendChild(el("h3", { textContent: "黃金存摺換算試算" }));
  wrap.appendChild(el("p", { textContent: "試算價：" + fmt(pb.latest_price, 1) + " " + pb.unit }));
  if (pb.pullback_stage) {
    wrap.appendChild(el("p", {
      textContent: "近 30 天高點 " + fmt(pb.pullback_stage.recent_high, 1) + "，目前回檔 " + fmt(pb.pullback_stage.pullback_pct, 1) + "%，對照存摺紀律大概落在：" + pb.pullback_stage.stage,
    }));
  } else if (pb.relative_position) {
    wrap.appendChild(el("p", { textContent: "區間位置：" + fmt(pb.relative_position.position * 100, 0) + "%（" + pb.relative_position.label + "）" }));
  } else {
    wrap.appendChild(el("p", { className: "source-note", textContent: "資料不足，無法計算相對位置。" }));
  }
  if (pb.technical_score) {
    wrap.appendChild(el("p", { textContent: "技術面：" + pb.technical_score.label + "（" + fmt(pb.technical_score.composite, 0) + " 分）" }));
  }
  wrap.appendChild(el("p", { className: "source-note", textContent: pb.note }));
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

function renderAiSummary(target) {
  const summary = target.ai_summary;
  if (!summary) return null;
  const wrap = el("div", { className: "ai-summary-card" });
  wrap.appendChild(el("h3", { textContent: "AI 推測：今天可能的漲跌原因" }));
  wrap.appendChild(el("p", { textContent: summary.text }));
  wrap.appendChild(el("p", { className: "source-note", textContent: summary.disclaimer }));
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
    const checkedTime = formatBuildCheckedTime();
    const checkedNote = checkedTime ? "，" + checkedTime + " 查核仍為最新" : "";
    priceLine.appendChild(document.createTextNode("最新收盤：" + fmt(target.latest.close, 2) + "（" + target.latest.trading_date + "，非即時" + checkedNote + "）"));
  }
  card.appendChild(priceLine);
  if (target.quote_delay_note) {
    card.appendChild(el("p", { className: "source-note", textContent: target.quote_delay_note }));
  }
  const passbookNode = renderPassbookEstimate(target);
  if (passbookNode) card.appendChild(passbookNode);
  const scoreNode = renderTechnicalScore(target);
  if (scoreNode) card.appendChild(scoreNode);
  const aiSummaryNode = renderAiSummary(target);
  if (aiSummaryNode) card.appendChild(aiSummaryNode);
  card.appendChild(renderDisciplineCard(target));
  const grid = el("div", { className: "indicator-grid" });
  const signals = target.indicators.stage_signals;
  grid.appendChild(indicatorCard("rsi14", "RSI14", fmt(target.indicators.rsi14, 2), signals ? signalBadgeParagraph("止跌判斷（RSI回升+近3天無新低）", signals.momentum_stabilizing) : null));
  grid.appendChild(indicatorCard("bollinger", "布林通道", fmt(target.indicators.bollinger.lower, 2) + " ~ " + fmt(target.indicators.bollinger.upper, 2)));
  const vs = target.indicators.volatility_squeeze;
  const vsText = vs.ratio === null ? "資料不足" : fmt(vs.ratio, 2) + (vs.is_compressed ? "（壓縮中）" : "");
  grid.appendChild(indicatorCard("volatility_squeeze", "波動壓縮比", vsText));
  const rp = target.indicators.relative_position;
  const rpText = rp === null ? "資料不足" : fmt(rp.position * 100, 0) + "%（" + rp.label + "）";
  grid.appendChild(indicatorCard("relative_position", "區間位置", rpText, renderPositionGauge(rp)));
  grid.appendChild(indicatorCard("ma20", "MA20", fmt(target.indicators.ma20, 2), signals ? signalBadgeParagraph("趨勢沒壞判斷（站上MA20+未破前低）", signals.trend_intact) : null));
  grid.appendChild(indicatorCard("macd", "MACD", fmt(target.indicators.macd.macd, 4) + " / 訊號線 " + fmt(target.indicators.macd.signal, 4), signals ? signalBadgeParagraph("重新轉強判斷（MACD黃金交叉）", signals.macd_golden_cross) : null));
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
      markPageUpdatedNow();
    })
    .catch(() => {});
}

function refreshLiveQuotes() {
  fetch("data/live_quotes.json?ts=" + Date.now())
    .then((res) => (res.ok ? res.json() : null))
    .then((fresh) => {
      if (!fresh) return;
      SITE_DATA.live_quotes_generated_at = fresh.generated_at;
      if (fresh.quotes) {
        Object.keys(fresh.quotes).forEach((code) => {
          const target = SITE_DATA.targets[code];
          if (target) Object.assign(target.latest, fresh.quotes[code]);
        });
      }
      // 就算這次沒有任何標的的報價值真的變，查核時間本身也是新的，
      // 一律重繪目前分頁，讓「查核仍為最新」的時間跟著這次輪詢往前走。
      if (currentTargetCode) renderTarget(currentTargetCode);
      markPageUpdatedNow();
    })
    .catch(() => {});
}

renderDisclaimer();
renderTargetTabs();
renderTarget(Object.keys(SITE_DATA.targets)[0]);
renderMacro();
markPageUpdatedNow();
if (SITE_DATA.auto_refresh_seconds) {
  setInterval(refreshSiteData, SITE_DATA.auto_refresh_seconds * 1000);
}
if (SITE_DATA.live_refresh_seconds) {
  setInterval(refreshLiveQuotes, SITE_DATA.live_refresh_seconds * 1000);
}
</script>
</body>
</html>
"""


def render_html(payload: dict) -> str:
    data_json = json.dumps(payload, ensure_ascii=False)
    return HTML_TEMPLATE.replace("__DATA__", data_json)


def _fetch_bars_with_fallback(
    code: str, year: int, month: int, min_bars: int = 30
) -> list[DailyBar]:
    """抓當月資料，不足 min_bars 筆時往前逐月追加，最多再往回抓 3 個月。

    TWSE STOCK_DAY 對「這個月還沒有資料」不是回傳空陣列，而是丟
    TwseFetchError，要接住才能正常 fallback。剛跨月的第一天更是連上個月
    單獨算都不夠 30 筆（一個月大約 20 出頭個交易日），所以要能連續往回
    追加，而不是只 fallback 一次就停。
    """
    try:
        bars = fetch_month(code, year, month)
    except TwseFetchError:
        bars = []
    cursor_year, cursor_month = year, month
    for _ in range(3):
        if len(bars) >= min_bars:
            break
        cursor_month -= 1
        if cursor_month < 1:
            cursor_month = 12
            cursor_year -= 1
        try:
            older_bars = fetch_month(code, cursor_year, cursor_month)
        except TwseFetchError:
            older_bars = []
        bars = older_bars + bars
    return bars


def _publish_static_cover() -> None:
    """把手刻的封面頁（static/cover.html + assets）複製成 site/index.html。

    封面頁是手刻的靜態內容，不是每次建置動態產生的，所以直接複製檔案，
    讓它在每次排程重建後都還在；實際的儀表板改放在 dashboard.html，
    封面頁的按鈕點下去會連過去。
    """
    if not (STATIC_DIR / "cover.html").exists():
        return
    shutil.copyfile(STATIC_DIR / "cover.html", SITE_DIR / "index.html")
    static_assets = STATIC_DIR / "assets"
    if static_assets.exists():
        shutil.copytree(static_assets, SITE_DIR / "assets", dirs_exist_ok=True)


def _fetch_published_notify_state() -> dict[str, dict]:
    """讀回已公開的 notify_state.json；抓不到（第一次執行、暫時性錯誤）就當空白重來。"""
    try:
        response = requests.get(PUBLISHED_NOTIFY_STATE_URL, timeout=10)
        if response.status_code != 200:
            return {}
        return response.json()
    except (requests.RequestException, ValueError):
        return {}


def _is_twse_trading_session(now: datetime) -> bool:
    """`now` 是否落在 TWSE 平日交易時段附近（含開盤前／收盤後緩衝）。"""
    local = now.astimezone(TAIPEI_TZ)
    if local.weekday() >= 5:  # 5=Sat, 6=Sun
        return False
    return TWSE_SESSION_START <= local.time() <= TWSE_SESSION_END


def should_fetch_twse(now: datetime) -> bool:
    """是否該真的去打 TWSE——交易時段內，或手動設定 FORCE_TWSE_FETCH 強制補資料時。

    build_site.py、update_quotes.py 都要用同一套判斷，共用這裡避免兩邊各自
    重複、之後又不小心漏改其中一邊。
    """
    return _is_twse_trading_session(now) or os.environ.get("FORCE_TWSE_FETCH") == "true"


def _fetch_published_site_data() -> dict:
    """讀回已公開的 gold_site.json；抓不到就回傳空字典，呼叫端自行處理。"""
    try:
        response = requests.get(PUBLISHED_SITE_DATA_URL, timeout=10)
        if response.status_code != 200:
            return {}
        return response.json()
    except (requests.RequestException, ValueError):
        return {}


def main() -> None:
    now = datetime.now(UTC).astimezone()
    year, month = now.year, now.month
    twse_trading_session = should_fetch_twse(now)

    published_site_data: dict | None = None

    def _published_target(code: str) -> dict | None:
        nonlocal published_site_data
        if published_site_data is None:
            published_site_data = _fetch_published_site_data()
        return published_site_data.get("targets", {}).get(code)

    # 非交易時間直接跳過 TWSE 即時抓取，改沿用上次成功發布的該標的資料；交易
    # 時間內如果真的抓不到、或抓到的天數不夠算 30 天指標（例如往回抓 3 個月
    # fallback 時剛好每個月都撞到 TWSE 502，只湊到當月幾天），一樣退回上次
    # 發布的資料，不讓網站從「完整歷史」退化成「資料不足」——只有連上次發布
    # 的資料都沒有（例如第一次執行）才會將就用這次抓到的不完整資料，總比
    # 完全沒有東西可顯示好。
    MIN_BARS_FOR_INDICATORS = 30
    twse_bars: dict[str, list[DailyBar]] = {}
    stale_targets: dict[str, dict] = {}
    for code in TARGET_META:
        bars = (
            _fetch_bars_with_fallback(code, year, month) if twse_trading_session else []
        )
        if len(bars) >= MIN_BARS_FOR_INDICATORS:
            twse_bars[code] = bars
            continue
        published = _published_target(code)
        if published is None:
            if not bars:
                raise RuntimeError(
                    f"{code}: no TWSE bars fetched for the requested months and no "
                    "previously published data to fall back to"
                )
            twse_bars[code] = bars
            continue
        stale_targets[code] = published

    macro_gold = fetch_series("GC=F", DATA_CACHE_DIR / "gc_f.json")
    macro_fx = fetch_series("USDTWD=X", DATA_CACHE_DIR / "usdtwd.json")

    twse_realtime = (
        {code: fetch_realtime_quote(code) for code in twse_bars}
        if twse_trading_session
        else {}
    )
    intl_gold_realtime = fetch_latest_price("GC=F")

    payload = build_payload(
        twse_bars, macro_gold, macro_fx, now, twse_realtime, intl_gold_realtime
    )
    payload["targets"].update(stale_targets)

    (SITE_DIR / "data").mkdir(parents=True, exist_ok=True)
    (SITE_DIR / "data" / "gold_site.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (SITE_DIR / "dashboard.html").write_text(render_html(payload), encoding="utf-8")
    _publish_static_cover()

    previous_notify_state = _fetch_published_notify_state()
    new_notify_state = check_and_notify(payload["targets"], previous_notify_state)
    write_notify_state(SITE_DIR / "notify_state.json", new_notify_state)


if __name__ == "__main__":
    main()
