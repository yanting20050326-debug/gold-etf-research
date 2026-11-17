import json
from datetime import date

import requests

from gold_research.llm_summary import (
    generate_daily_summary,
    redact_api_key,
    restore_cache_from_published,
)


def test_generate_daily_summary_calls_gemini_and_caches(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    cache_path = tmp_path / "summary_00635U.json"

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "candidates": [
                    {"content": {"parts": [{"text": "美元走弱，帶動金價走高。"}]}}
                ]
            }

    def fake_post(url, params, json, timeout):
        assert params["key"] == "fake-key"
        return FakeResponse()

    monkeypatch.setattr("gold_research.llm_summary.requests.post", fake_post)
    summary = generate_daily_summary(
        "元大S&P黃金", 1.2, 0.8, -0.3, "技術面中性", cache_path, date(2026, 8, 27)
    )
    assert summary.text == "美元走弱，帶動金價走高。"
    assert summary.generated_date == "2026-08-27"
    assert cache_path.exists()


def test_generate_daily_summary_uses_cache_when_already_generated_today(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    cache_path = tmp_path / "summary.json"
    cache_path.write_text(
        json.dumps({"text": "昨天的推測", "generated_date": "2026-08-27"}),
        encoding="utf-8",
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("should not call the API when cache is already fresh")

    monkeypatch.setattr("gold_research.llm_summary.requests.post", fail_if_called)
    summary = generate_daily_summary(
        "元大S&P黃金", 1.0, 1.0, 1.0, "中性", cache_path, date(2026, 8, 27)
    )
    assert summary.text == "昨天的推測"


def test_generate_daily_summary_regenerates_on_new_day(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    cache_path = tmp_path / "summary.json"
    cache_path.write_text(
        json.dumps({"text": "昨天的推測", "generated_date": "2026-08-26"}),
        encoding="utf-8",
    )

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "candidates": [{"content": {"parts": [{"text": "今天的新推測。"}]}}]
            }

    monkeypatch.setattr(
        "gold_research.llm_summary.requests.post",
        lambda url, params, json, timeout: FakeResponse(),
    )
    summary = generate_daily_summary(
        "元大S&P黃金", 1.0, 1.0, 1.0, "中性", cache_path, date(2026, 8, 27)
    )
    assert summary.text == "今天的新推測。"


def test_generate_daily_summary_returns_none_without_api_key_and_no_cache(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cache_path = tmp_path / "summary.json"
    assert (
        generate_daily_summary(
            "元大S&P黃金", 1.0, 1.0, 1.0, "中性", cache_path, date(2026, 8, 27)
        )
        is None
    )


def test_generate_daily_summary_falls_back_to_cache_on_api_failure(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    cache_path = tmp_path / "summary.json"
    cache_path.write_text(
        json.dumps({"text": "昨天的推測", "generated_date": "2026-08-26"}),
        encoding="utf-8",
    )

    def raise_error(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr("gold_research.llm_summary.requests.post", raise_error)
    summary = generate_daily_summary(
        "元大S&P黃金", 1.0, 1.0, 1.0, "中性", cache_path, date(2026, 8, 27)
    )
    assert summary.text == "昨天的推測"


def test_generate_daily_summary_retries_once_after_a_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    cache_path = tmp_path / "summary.json"
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "重試成功。"}]}}]}

    def flaky_post(url, params, json, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise requests.ReadTimeout("read timed out")
        return FakeResponse()

    monkeypatch.setattr("gold_research.llm_summary.requests.post", flaky_post)
    summary = generate_daily_summary(
        "元大S&P黃金", 1.0, 1.0, 1.0, "中性", cache_path, date(2026, 8, 27)
    )
    assert summary.text == "重試成功。"
    assert len(calls) == 2
    # 20 秒在 Actions 上幾乎都逾時，逾時值不能再退回去。
    assert all(timeout >= 45 for timeout in calls)


def test_redact_api_key_masks_the_key_in_request_error_messages():
    message = (
        "401 Client Error: Unauthorized for url: "
        "https://generativelanguage.googleapis.com/v1beta/models/m:generateContent"
        "?key=SECRET-VALUE-123"
    )
    redacted = redact_api_key(message)
    assert "SECRET-VALUE-123" not in redacted
    assert "key=***" in redacted
    assert "generateContent" in redacted  # 其餘訊息保留，仍可除錯


def test_failed_calls_never_log_the_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "SECRET-VALUE-123")
    logged = []
    monkeypatch.setattr(
        "gold_research.llm_summary.logger.warning",
        lambda template, *args: logged.append(template.format(*args)),
    )

    def unauthorized(url, params, json, timeout):
        raise requests.HTTPError(f"401 for url: {url}?key={params['key']}")

    monkeypatch.setattr("gold_research.llm_summary.requests.post", unauthorized)
    generate_daily_summary(
        "元大S&P黃金", 1.0, 1.0, 1.0, "中性", tmp_path / "c.json", date(2026, 8, 27)
    )
    assert logged
    assert not any("SECRET-VALUE-123" in line for line in logged)


def test_restore_cache_from_published_fills_missing_caches_only(tmp_path):
    (tmp_path / "ai_summary_GCF.json").write_text(
        json.dumps({"text": "本機較新", "generated_date": "2026-11-17"}),
        encoding="utf-8",
    )
    published = {
        "GCF": {
            "ai_summary": {"text": "已發布的舊摘要", "generated_date": "2026-11-16"}
        },
        "00635U": {"ai_summary": {"text": "ETF 摘要", "generated_date": "2026-11-17"}},
        "00708L": {"ai_summary": {"text": "舊版沒有日期"}},
        "NONE": {"ai_summary": None},
    }
    restored = restore_cache_from_published(
        published, tmp_path, "2026-11-15T03:00:00+00:00"
    )
    assert restored == 2
    gcf = json.loads((tmp_path / "ai_summary_GCF.json").read_text(encoding="utf-8"))
    assert gcf["text"] == "本機較新"  # 已有快取就不覆蓋
    etf = json.loads((tmp_path / "ai_summary_00635U.json").read_text(encoding="utf-8"))
    assert etf == {"text": "ETF 摘要", "generated_date": "2026-11-17"}
    legacy = json.loads(
        (tmp_path / "ai_summary_00708L.json").read_text(encoding="utf-8")
    )
    # 舊版資料沒有 generated_date：用已發布檔的建置日，今天仍會嘗試重新產生。
    assert legacy["generated_date"] == "2026-11-15"
    assert not (tmp_path / "ai_summary_NONE.json").exists()


def test_restored_cache_prevents_a_second_api_call_the_same_day(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    restore_cache_from_published(
        {
            "00635U": {
                "ai_summary": {"text": "今天已產生", "generated_date": "2026-11-17"}
            }
        },
        tmp_path,
        None,
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("今天已有摘要，不該再呼叫 Gemini")

    monkeypatch.setattr("gold_research.llm_summary.requests.post", fail_if_called)
    summary = generate_daily_summary(
        "元大S&P黃金",
        1.0,
        1.0,
        1.0,
        "中性",
        tmp_path / "ai_summary_00635U.json",
        date(2026, 11, 17),
    )
    assert summary.text == "今天已產生"
