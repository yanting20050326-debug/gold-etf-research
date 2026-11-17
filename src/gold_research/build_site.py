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
from loguru import logger

from gold_research.fetch_macro import (
    LatestQuote,
    MacroSeries,
    PricePoint,
    fetch_latest_price,
    fetch_series,
)
from gold_research.fetch_tbb_gold import (
    PassbookQuote,
    TbbGoldFetchError,
    fetch_current_quote,
    fetch_history,
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
    has_broken_swing_low,
    macd,
    macd_golden_cross,
    momentum_stabilizing,
    prior_swing_low,
    pullback_stage,
    relative_position,
    relative_strength_index,
    simple_moving_average,
    technical_score,
    trend_still_intact,
    volatility_squeeze,
)
from gold_research.llm_summary import (
    AI_SUMMARY_DISCLAIMER,
    generate_daily_summary,
    restore_cache_from_published,
)
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

TBB_CODE = "TBBGOLD"
TBB_META = {
    "display_name": "台企銀黃金存摺",
    "asset_class": "bank_gold_passbook",
    "asset_class_note": (
        "臺灣企銀黃金存摺的公告牌價（新台幣／公克），價格跟著國際金價與美元兌台幣"
        "走，但由銀行自行掛牌、一天會更新數次。本站的回檔階段與技術指標都用"
        "「本行賣出價」（你向銀行買進的價格）計算；買進價（你賣回給銀行的價格）"
        "比賣出價低，兩者的差距就是進出一趟的成本。牌價資料來自銀行公開頁面，"
        "僅供參考，實際交易價格以銀行交易確認時顯示的為準。"
    ),
    "badge": "銀行牌價，非即時行情",
}
MIN_TBB_POINTS = 30

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SITE_DIR = PROJECT_ROOT / "site"
DATA_CACHE_DIR = PROJECT_ROOT / "data_cache"
STATIC_DIR = PROJECT_ROOT / "static"

# GitHub Actions 排程每次都在全新、跑完就丟棄的機器上執行，本機檔案不會保留到
# 下一次執行；LINE 通知需要記得「上次通知過的階段」才能只在真的變化時發送，
# 所以直接讀回已公開的 notify_state.json 當作狀態來源，寫入時一併放進
# site/ 目錄，讓它跟著這次建置結果一起發布到公開 repo，下次執行再讀回來。
# 即時報價 Worker（quote-worker/）：收到請求才抓 TWSE／Yahoo，快取 10 秒，
# 網頁輪詢它取得報價。網址是 *.workers.dev 的公開唯讀端點，不含任何機密。
LIVE_QUOTES_URL = "https://gold-quote-worker.abilabnpust.workers.dev/"
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
    # 找不到前低 ≠ 資料不足：若過去有過低點卻全被跌破，代表價格已在所有前低之下，
    # 「趨勢沒壞」就是不成立（False），不是無法判斷（None）。
    prior_low_broken = prior_low is None and has_broken_swing_low(closes, 30)
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
        "prior_low_broken": prior_low_broken,
        "technical_score": technical_score(
            close=closes[-1],
            ma20=sma20[-1],
            rsi14=rsi14[-1],
            bollinger={"upper": bands["upper"][-1], "lower": bands["lower"][-1]},
            macd_value=macd_result["macd"][-1],
        ),
        "stage_signals": {
            "trend_intact": (
                False
                if prior_low_broken
                else trend_still_intact(
                    closes[-1], sma20[-1], prior_low["price"] if prior_low else None
                )
            ),
            "momentum_stabilizing": momentum_stabilizing(closes),
            "macd_golden_cross": macd_golden_cross(closes),
        },
    }


def _build_latest(
    trading_date: str,
    close: float,
    volume: int | None,
    realtime: object | None,
    prev_close: float | None = None,
) -> dict:
    """組裝 `latest` 欄位；有即時報價就用即時報價覆蓋顯示，沒有就用日線收盤。

    `prev_close` 是算「今天漲跌幅」的基準價：TWSE 即時報價自帶昨收（`y`）就直接
    用；其餘情況由呼叫端傳入前一根日線的收盤價。
    """
    if realtime is None:
        return {
            "trading_date": trading_date,
            "close": close,
            "volume": volume,
            "quote_time": None,
            "is_realtime": False,
            "prev_close": prev_close,
        }
    if isinstance(realtime, RealtimeQuote):
        return {
            "trading_date": trading_date,
            "close": realtime.price,
            "volume": volume,
            "quote_time": realtime.quote_time,
            "is_realtime": True,
            "prev_close": realtime.previous_close,
        }
    if isinstance(realtime, LatestQuote):
        return {
            "trading_date": trading_date,
            "close": realtime.price,
            "volume": volume,
            "quote_time": realtime.quote_time,
            "is_realtime": True,
            "prev_close": prev_close,
        }
    raise TypeError(f"unsupported realtime quote type: {type(realtime)!r}")


def _pct_change(prev: float | None, curr: float | None) -> float | None:
    if prev is None or curr is None or prev == 0:
        return None
    return (curr - prev) / prev * 100


def _ai_summary_field(ai_summary) -> dict | None:
    if ai_summary is None:
        return None
    return {
        "text": ai_summary.text,
        "generated_date": ai_summary.generated_date,
        "disclaimer": AI_SUMMARY_DISCLAIMER,
    }


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
            latest_bar.trading_date,
            latest_bar.close,
            latest_bar.volume,
            realtime,
            bars[-2].close if len(bars) >= 2 else None,
        ),
        "indicators": indicators,
        "chart": [
            {"trading_date": bar.trading_date, "close": bar.close} for bar in bars
        ],
        "ai_summary": _ai_summary_field(ai_summary),
    }


def apply_quote_to_series(
    series: list[tuple[str, float]], quote: PassbookQuote | None
) -> list[tuple[str, float]]:
    """把當日最新牌價放進歷史序列：同一天就覆蓋（盤中牌價會更新），新的一天就追加。

    序列是（日期, 賣出價）。歷史表的最後一列通常已經是今天，但盤中重新掛牌後
    以當日牌價頁為準，兩邊才不會差一個掛牌時點。
    """
    if quote is None or not series:
        return list(series)
    last_date = series[-1][0]
    if quote.listed_date == last_date:
        return series[:-1] + [(last_date, quote.sell)]
    if quote.listed_date > last_date:
        return series + [(quote.listed_date, quote.sell)]
    return list(series)  # 當日牌價比歷史還舊（不應發生），不要把序列倒退


def _build_tbb_target(
    series: list[tuple[str, float]],
    quote: PassbookQuote | None,
    intl_gold_change_pct: float | None,
    usdtwd_change_pct: float | None,
    today: date,
    cache_dir: Path,
) -> dict:
    """台企銀黃金存摺：指標、回檔階段一律用「賣出價」（你向銀行買進的價格）。"""
    series = apply_quote_to_series(series, quote)
    closes = [price for _, price in series]
    last_date, last_price = series[-1]
    prev_price = series[-2][1] if len(series) >= 2 else None
    indicators = _compute_indicators(closes)
    technical_label = (
        indicators["technical_score"]["label"]
        if indicators["technical_score"]
        else None
    )
    ai_summary = generate_daily_summary(
        TBB_META["display_name"],
        _pct_change(prev_price, last_price),
        intl_gold_change_pct,
        usdtwd_change_pct,
        technical_label,
        cache_dir / f"ai_summary_{TBB_CODE}.json",
        today,
    )
    target = {
        "code": TBB_CODE,
        "display_name": TBB_META["display_name"],
        "asset_class": TBB_META["asset_class"],
        "asset_class_note": TBB_META["asset_class_note"],
        "badge": TBB_META["badge"],
        "pullback_scale": 1.0,
        "price_digits": 0,  # 牌價以整數元／公克掛牌
        "unit": "元／公克",
        "data_source": {"name": "臺灣企銀黃金存摺牌價", "as_of": last_date},
        "latest": _build_latest(last_date, last_price, None, None, prev_price),
        "indicators": indicators,
        "chart": [{"trading_date": d, "close": p} for d, p in series],
        "ai_summary": _ai_summary_field(ai_summary),
    }
    if quote is not None:
        target["passbook_quote"] = {
            "sell": quote.sell,
            "buy": quote.buy,
            "spread": quote.sell - quote.buy,
            "listed_at": f"{quote.listed_date} {quote.listed_time}",
        }
    return target


def _build_intl_gold_target(
    points: list[PricePoint],
    as_of: str,
    realtime: LatestQuote | None,
    usdtwd_change_pct: float | None,
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
        "latest": _build_latest(
            latest_point.date,
            latest_point.close,
            None,
            realtime,
            points[-2].close if len(points) >= 2 else None,
        ),
        "indicators": indicators,
        "chart": [{"trading_date": p.date, "close": p.close} for p in points],
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
    tbb_series: list[tuple[str, float]] | None = None,
    tbb_quote: PassbookQuote | None = None,
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
            today,
            cache_dir,
        )
    if tbb_series and len(tbb_series) >= MIN_TBB_POINTS:
        targets[TBB_CODE] = _build_tbb_target(
            tbb_series,
            tbb_quote,
            intl_gold_change_pct,
            usdtwd_change_pct,
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
        "live_quotes_url": LIVE_QUOTES_URL,
        "targets": targets,
        "macro_context": {
            "data_source": {"name": "Yahoo Finance", "as_of": macro_fx.as_of},
            "usdtwd": [asdict(p) for p in macro_fx.points],
            "stale": macro_gold.stale or macro_fx.stale,
            "note": "總體背景參考，非本頁技術指標，僅供市場情緒判讀。",
            "divergence": divergence,
        },
    }


DASHBOARD_TEMPLATE_PATH = STATIC_DIR / "dashboard_template.html"


def _load_dashboard_template() -> str:
    """儀表板前端模板放在 static/dashboard_template.html，以 __DATA__ 占位符嵌入資料。"""
    return DASHBOARD_TEMPLATE_PATH.read_text(encoding="utf-8")


def render_html(payload: dict) -> str:
    data_json = json.dumps(payload, ensure_ascii=False)
    return _load_dashboard_template().replace("__DATA__", data_json)


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


def resolve_tbb_inputs(
    published: dict | None,
    fetch_quote=fetch_current_quote,
    fetch_history_days=fetch_history,
) -> tuple[list[tuple[str, float]] | None, PassbookQuote | None]:
    """決定台企銀牌價要用的（歷史序列, 當日牌價）。

    歷史表一天只會多一列，所以已發布的資料如果已經涵蓋到當日牌價的日期，就
    直接沿用它的序列、只重抓當日牌價（1 個請求），不要每次建站都去查歷史
    （2 個請求）——建站每 5 分鐘一次，對銀行公開頁面放低頻率才有禮貌。
    任何一步失敗都不讓建站中斷：當日牌價失敗就不更新牌價、歷史失敗就沿用已發布序列。
    """
    try:
        quote = fetch_quote()
    except TbbGoldFetchError as exc:
        logger.warning("台企銀當日牌價抓取失敗：{}", exc)
        quote = None

    published_series: list[tuple[str, float]] = []
    if published and published.get("chart"):
        published_series = [(p["trading_date"], p["close"]) for p in published["chart"]]

    covers_today = len(published_series) >= MIN_TBB_POINTS and (
        quote is None or quote.listed_date <= published_series[-1][0]
    )
    if covers_today:
        return published_series, quote
    try:
        days = fetch_history_days()
    except TbbGoldFetchError as exc:
        logger.warning("台企銀歷史牌價抓取失敗：{}，沿用已發布的序列", exc)
        return (published_series or None), quote
    return [(d.trading_date, d.sell) for d in days], quote


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

    # Actions 每次都從空的 data_cache 開始，AI 摘要的「一天只呼叫一次」快取因此
    # 形同虛設：Gemini 一逾時就把已發布的摘要洗成 null。先用已發布的摘要還原快取，
    # 今天已經產生過就不再呼叫，失敗時也能沿用舊摘要。
    if published_site_data is None:
        published_site_data = _fetch_published_site_data()
    restore_cache_from_published(
        published_site_data.get("targets", {}),
        DATA_CACHE_DIR,
        published_site_data.get("generated_at"),
    )

    # 非交易時間直接跳過 TWSE 即時抓取，改沿用上次成功發布的該標的資料；交易
    # 時間內如果真的抓不到、或抓到的天數不夠算 30 天指標（例如往回抓 3 個月
    # fallback 時剛好每個月都撞到 TWSE 502，只湊到當月幾天），一樣退回上次
    # 發布的資料，不讓網站從「完整歷史」退化成「資料不足」——只有連上次發布
    # 的資料都沒有（例如第一次執行）才會將就用這次抓到的不完整資料，總比
    # 完全沒有東西可顯示好。
    MIN_BARS_FOR_INDICATORS = 30
    # MACD 的訊號線要 26 + 9 = 35 根日線才算得出來；只抓 30 根時 ETF 的 MACD 一直
    # 顯示「資料不足」。多往前抓一個月湊到 40 根，上面的 30 根門檻維持不變。
    MIN_BARS_TO_FETCH = 40
    twse_bars: dict[str, list[DailyBar]] = {}
    stale_targets: dict[str, dict] = {}
    for code in TARGET_META:
        bars = (
            _fetch_bars_with_fallback(code, year, month, min_bars=MIN_BARS_TO_FETCH)
            if twse_trading_session
            else []
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

    tbb_series, tbb_quote = resolve_tbb_inputs(_published_target(TBB_CODE))

    payload = build_payload(
        twse_bars,
        macro_gold,
        macro_fx,
        now,
        twse_realtime,
        intl_gold_realtime,
        tbb_series=tbb_series,
        tbb_quote=tbb_quote,
    )
    payload["targets"].update(stale_targets)
    if TBB_CODE not in payload["targets"]:
        # 台企銀這次完全抓不到（歷史也沒有）：沿用已發布的，沒有就先不顯示這個標的。
        published_tbb = _published_target(TBB_CODE)
        if published_tbb:
            payload["targets"][TBB_CODE] = published_tbb

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
