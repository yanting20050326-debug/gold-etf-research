import json

import pytest
import requests

from gold_research.fetch_macro import fetch_series


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
