import json
from datetime import date

import requests

from gold_research.llm_summary import generate_daily_summary


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
