"""黃金相關研究站專用的資料新鮮度／schema 檢查。

跟 ETF 網站的 run_system_health_check.py 完全無關——不同專案、不同資料、
不共用任何假設。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

SITE_DATA_PATH = (
    Path(__file__).resolve().parent.parent.parent / "site" / "data" / "gold_site.json"
)
MAX_STALE_DAYS = 5


@dataclass(frozen=True)
class HealthIssue:
    severity: str  # "error" | "warning"
    message: str


def check_payload(payload: dict, today: date) -> list[HealthIssue]:
    issues: list[HealthIssue] = []

    for key in ("disclaimer", "targets", "macro_context", "candidates"):
        if key not in payload:
            issues.append(HealthIssue("error", f"缺少必要欄位：{key}"))
    if issues:
        return issues

    if "00635U" not in payload["targets"]:
        issues.append(HealthIssue("error", "targets 缺少 00635U"))
    else:
        target = payload["targets"]["00635U"]
        as_of_str = target.get("data_source", {}).get("as_of")
        issues.extend(_check_staleness(as_of_str, today, "00635U 價格"))

    macro_as_of = payload.get("macro_context", {}).get("data_source", {}).get("as_of")
    issues.extend(_check_staleness(macro_as_of, today, "總體背景參考"))

    if payload.get("macro_context", {}).get("stale"):
        issues.append(
            HealthIssue("warning", "總體背景參考目前顯示的是快取資料（上次抓取失敗）")
        )

    if len(payload.get("candidates", [])) == 0:
        issues.append(HealthIssue("warning", "候選標的清單是空的"))

    return issues


def _check_staleness(
    as_of_str: str | None, today: date, label: str
) -> list[HealthIssue]:
    if not as_of_str:
        return [HealthIssue("error", f"{label} 沒有 as_of 日期")]
    as_of = datetime.strptime(  # noqa: DTZ007 -- date-only comparison, no tz needed
        as_of_str, "%Y-%m-%d"
    ).date()
    age_days = (today - as_of).days
    if age_days > MAX_STALE_DAYS:
        return [
            HealthIssue(
                "warning", f"{label} 資料已經 {age_days} 天沒更新（{as_of_str}）"
            )
        ]
    return []


def main() -> int:
    if not SITE_DATA_PATH.exists():
        print(f"找不到 {SITE_DATA_PATH}，請先執行 build_site.py")
        return 1
    payload = json.loads(SITE_DATA_PATH.read_text(encoding="utf-8"))
    issues = check_payload(
        payload, date.today()  # noqa: DTZ011 -- date-only freshness check, no tz needed
    )
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    for issue in errors:
        print(f"[ERROR] {issue.message}")
    for issue in warnings:
        print(f"[WARNING] {issue.message}")
    if not issues:
        print("健康檢查通過，沒有發現問題。")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
