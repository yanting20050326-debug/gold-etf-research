"""用 Google Gemini 免費額度幫每個標的產生「今天可能為什麼漲跌」的推測摘要。

**沒有接真實新聞來源。** 這裡是把價格變化、國際金價/匯率變化、技術面狀態丟給
Gemini，讓它根據這些數字寫一段合理推測，不是引用真實新聞報導——這點在網頁上
也會明確標示，避免看起來像是真的新聞摘要。

每個標的一天只真的呼叫一次 API（用本地日期快取擋掉重複呼叫），原因：
1. 排程任務每 5 分鐘重跑一次 build_site.py，若每次都呼叫會很快用光免費額度。
2. 同一天多次呼叫可能得到互相矛盾的推測，對使用者沒有幫助。

沒有設定 GEMINI_API_KEY 或呼叫失敗時一律回傳既有快取（可能是 None），
不讓整個網站產生流程中斷。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import requests
from loguru import logger

GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)

AI_SUMMARY_DISCLAIMER = "AI 根據價格與總體數字推測，非真實新聞報導，僅供研究參考。"


@dataclass(frozen=True)
class AiSummary:
    text: str
    generated_date: str  # YYYY-MM-DD


def generate_daily_summary(
    display_name: str,
    price_change_pct: float | None,
    intl_gold_change_pct: float | None,
    usdtwd_change_pct: float | None,
    technical_label: str | None,
    cache_path: Path,
    today: date,
) -> AiSummary | None:
    """幫 `display_name` 這個標的產生今天的漲跌推測摘要，一天只真的呼叫一次。"""
    cached = _read_cache(cache_path)
    if cached is not None and cached.generated_date == today.isoformat():
        return cached

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY 未設定，略過 AI 摘要（{}）", display_name)
        return cached

    prompt = _build_prompt(
        display_name,
        price_change_pct,
        intl_gold_change_pct,
        usdtwd_change_pct,
        technical_label,
    )
    try:
        response = requests.post(
            GEMINI_API_URL,
            params={"key": api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        text = payload["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (
        requests.RequestException,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
    ) as exc:
        logger.warning("{} AI 摘要產生失敗：{}，沿用舊快取", display_name, exc)
        return cached

    summary = AiSummary(text=text, generated_date=today.isoformat())
    _write_cache(cache_path, summary)
    return summary


def _build_prompt(
    display_name: str,
    price_change_pct: float | None,
    intl_gold_change_pct: float | None,
    usdtwd_change_pct: float | None,
    technical_label: str | None,
) -> str:
    lines = [
        f"你是一個黃金市場分析助理。以下是「{display_name}」今天的數字變化：",
        f"- 標的自身漲跌幅：{_fmt_pct(price_change_pct)}",
        f"- 國際金價漲跌幅：{_fmt_pct(intl_gold_change_pct)}",
        f"- 美元兌台幣（USD/TWD）漲跌幅：{_fmt_pct(usdtwd_change_pct)}",
        f"- 技術面狀態：{technical_label or '資料不足'}",
        "",
        "請用繁體中文寫 2-3 句話，根據這些數字推測今天可能的漲跌原因",
        "（例如美元走弱、避險情緒、技術面過熱等一般性關聯）。",
        "只能根據這些數字做合理推測，不要假裝知道真實新聞或具體事件，",
        "也不要給任何買賣建議。開頭請直接寫推測內容，不要加開場白。",
    ]
    return "\n".join(lines)


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "資料不足"
    return f"{value:+.2f}%"


def _read_cache(cache_path: Path) -> AiSummary | None:
    if not cache_path.exists():
        return None
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        return AiSummary(text=raw["text"], generated_date=raw["generated_date"])
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def _write_cache(cache_path: Path, summary: AiSummary) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {"text": summary.text, "generated_date": summary.generated_date},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
