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
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import requests
from loguru import logger

GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-3.6-flash:generateContent"
)

GEMINI_TIMEOUT_SECONDS = 60
GEMINI_ATTEMPTS = 2

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
    text = _call_gemini(display_name, api_key, prompt)
    if text is None:
        return cached

    summary = AiSummary(text=text, generated_date=today.isoformat())
    _write_cache(cache_path, summary)
    return summary


def _call_gemini(display_name: str, api_key: str, prompt: str) -> str | None:
    """呼叫 Gemini；逾時或暫時性錯誤重試一次，最後仍失敗就回傳 None。

    實測 20 秒的讀取逾時在 Actions 上三個標的幾乎都會逾時，所以放寬到
    GEMINI_TIMEOUT_SECONDS；仍失敗時交給呼叫端沿用舊快取，不讓建站中斷。
    """
    last_error = ""
    for attempt in range(GEMINI_ATTEMPTS):
        try:
            response = requests.post(
                GEMINI_API_URL,
                params={"key": api_key},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=GEMINI_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
            return payload["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (
            requests.RequestException,
            ValueError,
            KeyError,
            IndexError,
            TypeError,
        ) as exc:
            last_error = redact_api_key(str(exc))
            logger.warning(
                "{} AI 摘要第 {}/{} 次嘗試失敗：{}",
                display_name,
                attempt + 1,
                GEMINI_ATTEMPTS,
                last_error,
            )
    logger.warning("{} AI 摘要產生失敗：{}，沿用舊快取", display_name, last_error)
    return None


def redact_api_key(message: str) -> str:
    """遮掉訊息裡的 `key=...`。

    requests 的 HTTPError／逾時訊息會原樣帶出完整網址，而 Gemini 的金鑰就放在
    網址的 query string，直接記錄會把金鑰寫進日誌。
    """
    return re.sub(r"([?&]key=)[^&\s)\"']+", r"\1***", message)


def restore_cache_from_published(
    published_targets: dict, cache_dir: Path, generated_at_iso: str | None
) -> int:
    """Actions 每次都從空的 data_cache 開始，所以先用已發布的摘要把快取補回來。

    只補「本機還沒有快取檔」的標的，回傳補了幾個。已發布資料若有
    `generated_date` 就沿用；舊版資料沒有這個欄位時，用已發布檔本身的建置日期
    當作產生日（比今天舊，所以今天仍會嘗試重新產生，但在成功之前頁面不會變空白）。
    """
    restored = 0
    fallback_date = (generated_at_iso or "1970-01-01")[:10]
    for code, target in published_targets.items():
        summary = target.get("ai_summary") if isinstance(target, dict) else None
        if not isinstance(summary, dict) or not summary.get("text"):
            continue
        cache_path = cache_dir / f"ai_summary_{code}.json"
        if cache_path.exists():
            continue
        _write_cache(
            cache_path,
            AiSummary(
                text=summary["text"],
                generated_date=summary.get("generated_date") or fallback_date,
            ),
        )
        restored += 1
    return restored


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
