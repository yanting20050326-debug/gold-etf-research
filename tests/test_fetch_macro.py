import json

import pytest
import requests

from gold_research.fetch_macro import LatestQuote, fetch_latest_price, fetch_series


def test_fetch_series_success_writes_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "gc_f.json"
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1734566400, 1734652800],
                    "indicators": {"quote": [{"close": [2050.1, 2055.4]}]},
                }
            ]
        }
    }

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(
        "gold_research.fetch_macro.requests.get",
        lambda url, params, timeout, headers: FakeResponse(),
    )
    series = fetch_series("GC=F", cache_path)
    assert series.stale is False
    assert len(series.points) == 2
    assert cache_path.exists()
    cached_payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached_payload["as_of"] == series.as_of


def test_fetch_series_falls_back_to_cache_on_network_failure(tmp_path, monkeypatch):
    cache_path = tmp_path / "gc_f.json"
    cache_path.write_text(
        json.dumps(
            {"as_of": "2026-08-19", "points": [{"date": "2026-08-19", "close": 2049.0}]}
        ),
        encoding="utf-8",
    )

    def raise_connection_error(url, params, timeout, headers):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(
        "gold_research.fetch_macro.requests.get", raise_connection_error
    )
    series = fetch_series("GC=F", cache_path)
    assert series.stale is True
    assert series.as_of == "2026-08-19"
    assert series.points[0].close == 2049.0


def test_fetch_series_raises_when_no_cache_available(tmp_path, monkeypatch):
    cache_path = tmp_path / "gc_f.json"  # 不存在

    def raise_connection_error(url, params, timeout, headers):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(
        "gold_research.fetch_macro.requests.get", raise_connection_error
    )
    with pytest.raises(requests.ConnectionError):
        fetch_series("GC=F", cache_path)


def test_fetch_series_empty_response_falls_back_to_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "gc_f.json"
    cache_path.write_text(
        json.dumps(
            {"as_of": "2026-08-18", "points": [{"date": "2026-08-18", "close": 2048.5}]}
        ),
        encoding="utf-8",
    )

    class FakeResponseEmpty:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "chart": {
                    "result": [
                        {
                            "timestamp": [],
                            "indicators": {"quote": [{"close": []}]},
                        }
                    ]
                }
            }

    monkeypatch.setattr(
        "gold_research.fetch_macro.requests.get",
        lambda url, params, timeout, headers: FakeResponseEmpty(),
    )
    series = fetch_series("GC=F", cache_path)
    assert series.stale is True
    assert series.as_of == "2026-08-18"
    assert series.points[0].close == 2048.5


def test_fetch_series_empty_response_raises_when_no_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "gc_f.json"  # 不存在

    class FakeResponseEmpty:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "chart": {
                    "result": [
                        {
                            "timestamp": [],
                            "indicators": {"quote": [{"close": []}]},
                        }
                    ]
                }
            }

    monkeypatch.setattr(
        "gold_research.fetch_macro.requests.get",
        lambda url, params, timeout, headers: FakeResponseEmpty(),
    )
    with pytest.raises(ValueError, match="returned no usable price points"):
        fetch_series("GC=F", cache_path)


def test_fetch_latest_price_returns_last_non_null_close(monkeypatch):
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1756000000, 1756000060, 1756000120],
                    "indicators": {"quote": [{"close": [2050.1, None, 2051.4]}]},
                }
            ]
        }
    }

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    def fake_get(url, params, timeout, headers):
        assert params["interval"] == "1m"
        assert params["range"] == "1d"
        return FakeResponse()

    monkeypatch.setattr("gold_research.fetch_macro.requests.get", fake_get)
    quote = fetch_latest_price("GC=F")
    # The last non-null close (index 2, value 2051.4) wins, not the last
    # timestamp overall — index 1's null must be skipped correctly.
    assert isinstance(quote, LatestQuote)
    assert quote.price == 2051.4
    # Always Taipei time regardless of the runner's own system timezone
    # (GitHub Actions defaults to UTC) — a bare .astimezone() used to leak
    # that ambient timezone straight into the displayed quote time with no
    # label, showing up as a confusing hour offset next to TWSE's
    # already-Taipei-local realtime quotes on the same page.
    assert quote.quote_time == "2025-08-24 09:48"


def test_fetch_latest_price_returns_none_when_all_closes_null(monkeypatch):
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1756000000],
                    "indicators": {"quote": [{"close": [None]}]},
                }
            ]
        }
    }

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(
        "gold_research.fetch_macro.requests.get",
        lambda url, params, timeout, headers: FakeResponse(),
    )
    assert fetch_latest_price("GC=F") is None


def test_fetch_latest_price_returns_none_on_network_error(monkeypatch):
    def raise_connection_error(url, params, timeout, headers):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(
        "gold_research.fetch_macro.requests.get", raise_connection_error
    )
    assert fetch_latest_price("GC=F") is None
