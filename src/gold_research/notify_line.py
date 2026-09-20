"""回檔階段到達加碼點時，透過 LINE Messaging API 的廣播訊息發通知。

用「廣播」而不是「推播給特定 userId」：抓自己的 LINE userId 需要另外架
webhook 才能拿到，對個人用的通知功能太重；只要這個官方帳號的好友只有
你自己，廣播訊息的效果就等同於「推播給我自己」，不用多一套 webhook
基礎設施。

沒有設定 LINE_CHANNEL_ACCESS_TOKEN 或呼叫失敗時，一律跳過通知，不讓
整個網站產生流程中斷（跟 llm_summary.py 的容錯設計一致）。訊息內容本身
要夠完整（標的名稱、回檔幅度、階段），因為收訊息的人不一定會馬上點進
網站看細節。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests
from loguru import logger

LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"

# 需要滿足前提條件才通知的階段，對照 build_site.py 的 stage_signals 欄位；
# 沒有列在這裡的可通知階段（目前只有「第一筆 20%」）代表沒有前提條件。
_STAGE_SIGNAL_KEY = {
    "加碼 20%（前提：趨勢沒壞）": "trend_intact",
    "加碼 20%（前提：出現止跌）": "momentum_stabilizing",
    "最後 40%（等重新轉強）": "macd_golden_cross",
}

_NOTIFIABLE_STAGES = {"第一筆 20%", *_STAGE_SIGNAL_KEY}


def should_notify_stage_change(
    previous_stage: str | None, current_stage: str, stage_signals: dict | None
) -> bool:
    """判斷「回檔階段」的變化是否構成一次加碼通知。"""
    if current_stage == previous_stage:
        return False
    if current_stage not in _NOTIFIABLE_STAGES:
        return False
    signal_key = _STAGE_SIGNAL_KEY.get(current_stage)
    if signal_key is None:
        return True
    if stage_signals is None:
        return False
    return stage_signals.get(signal_key) is True


def build_stage_change_message(
    display_name: str, stage: str, pullback_pct: float
) -> str:
    return (
        f"【{display_name}】回檔 {pullback_pct:.1f}%，"
        f"對照紀律已達「{stage}」，可考慮依你的紀律加碼。\n"
        "（自動通知，非投資建議，請自行判斷）"
    )


def build_prior_low_break_message(
    display_name: str, prior_low_price: float, close: float
) -> str:
    return (
        f"【{display_name}】收盤 {close:.2f} 已跌破起漲前低 {prior_low_price:.2f}，"
        "依你的紀律應停止加碼。\n"
        "（自動通知，非投資建議，請自行判斷）"
    )


def send_line_broadcast(message: str) -> bool:
    """呼叫 LINE Messaging API 廣播訊息；沒有 token 或失敗時回傳 False，不拋例外。"""
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        logger.warning("LINE_CHANNEL_ACCESS_TOKEN 未設定，略過 LINE 通知")
        return False
    try:
        response = requests.post(
            LINE_BROADCAST_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"messages": [{"type": "text", "text": message}]},
            timeout=10,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.warning("LINE 通知發送失敗：{}", exc)
        return False


def read_notify_state(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_notify_state(path: Path, state: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def check_and_notify(
    targets: dict[str, dict], previous_state: dict[str, dict]
) -> dict[str, dict]:
    """比對每個標的目前的回檔階段跟前一次記錄的狀態，決定是否要發通知。

    回傳更新後的 state（每個標的目前的階段與目前的起漲前低價），
    呼叫端負責寫檔持久化，下次執行時再讀回來當作 previous_state。
    """
    new_state: dict[str, dict] = {}
    for code, target in targets.items():
        indicators = target.get("indicators", {})
        pullback_stage = indicators.get("pullback_stage")
        if pullback_stage is None:
            continue
        stage = pullback_stage["stage"]
        prev = previous_state.get(code, {})
        stage_signals = indicators.get("stage_signals")

        if should_notify_stage_change(prev.get("stage"), stage, stage_signals):
            send_line_broadcast(
                build_stage_change_message(
                    target["display_name"], stage, pullback_stage["pullback_pct"]
                )
            )

        # prior_low 現在是「目前仍然有效、還沒被跌破的低點」（見
        # indicators.prior_swing_low），一旦真的跌破，當次執行就會馬上換成
        # 新的、更低的參考點，不會有「今天收盤 <= 這次算出來的 prior_low」
        # 的時刻——跌破那一刻，prior_low 本身已經不是原來那個值了。所以
        # 破位偵測要看的是「這次的 prior_low 價格，比上次記錄的低」，這才
        # 代表舊的參考點剛剛被跌破、換成新的。
        prior_low = indicators.get("prior_low")
        previous_prior_low_price = prev.get("prior_low_price")
        if (
            prior_low is not None
            and previous_prior_low_price is not None
            and prior_low["price"] < previous_prior_low_price
        ):
            latest_close = target.get("latest", {}).get("close")
            if latest_close is not None:
                send_line_broadcast(
                    build_prior_low_break_message(
                        target["display_name"], previous_prior_low_price, latest_close
                    )
                )

        new_state[code] = {
            "stage": stage,
            "prior_low_price": (
                prior_low["price"]
                if prior_low is not None
                else previous_prior_low_price
            ),
        }
    return new_state
