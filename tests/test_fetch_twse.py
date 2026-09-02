import pytest

from gold_research.fetch_twse import (
    DailyBar,
    RealtimeQuote,
    TwseFetchError,
    _parse_row,
    fetch_month,
    fetch_realtime_quote,
)


def test_parse_row_converts_roc_date_and_numbers():
    row = [
        "115/08/20",
        "12,345,678",
        "573,456,789",
        "46.30",
        "46.55",
        "46.10",
        "46.48",
        "+0.18",
        "3,210",
    ]
    bar = _parse_row(row)
    assert bar == DailyBar(
        trading_date="2026-08-20",
        open=46.30,
        high=46.55,
        low=46.10,
        close=46.48,
        volume=12345678,
    )


def test_fetch_month_parses_successful_response(monkeypatch):
    sample_payload = {
        "stat": "OK",
        "data": [
            [
                "115/08/19",
                "10,000,000",
                "463,000,000",
                "46.10",
                "46.40",
                "46.00",
                "46.30",
                "+0.10",
                "2,900",
            ],
            [
                "115/08/20",
                "12,345,678",
                "573,456,789",
                "46.30",
                "46.55",
                "46.10",
                "46.48",
                "+0.18",
                "3,210",
            ],
        ],
    }

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return sample_payload

    def fake_get(url, params, timeout):
        assert params["stockNo"] == "00635U"
        return FakeResponse()

    monkeypatch.setattr("gold_research.fetch_twse.requests.get", fake_get)
    bars = fetch_month("00635U", 2026, 8)
    assert len(bars) == 2
    assert bars[1].close == 46.48


def test_fetch_month_raises_on_bad_stat(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"stat": "很抱歉，沒有符合條件的資料!", "data": []}

    monkeypatch.setattr(
        "gold_research.fetch_twse.requests.get",
        lambda url, params, timeout: FakeResponse(),
    )
    with pytest.raises(TwseFetchError):
        fetch_month("00635U", 2020, 1)


def test_fetch_month_skips_unparseable_row(monkeypatch):
    sample_payload = {
        "stat": "OK",
        "data": [
            ["X", "X", "X", "X", "X", "X", "X", "X", "X"],
            [
                "115/08/20",
                "12,345,678",
                "573,456,789",
                "46.30",
                "46.55",
                "46.10",
                "46.48",
                "+0.18",
                "3,210",
            ],
        ],
    }

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return sample_payload

    monkeypatch.setattr(
        "gold_research.fetch_twse.requests.get",
        lambda url, params, timeout: FakeResponse(),
    )
    bars = fetch_month("00635U", 2026, 8)
    assert len(bars) == 1
    assert bars[0].close == 46.48


def test_fetch_realtime_quote_parses_real_response_shape(monkeypatch):
    # Shape matches an actual live response captured from the endpoint.
    sample_payload = {
        "msgArray": [
            {
                "c": "00635U",
                "z": "48.1000",
                "y": "48.0700",
                "d": "20260826",
                "t": "13:30:00",
            }
        ],
        "rtmessage": "OK",
    }

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return sample_payload

    def fake_get(url, params, headers, timeout):
        assert params["ex_ch"] == "tse_00635U.tw"
        return FakeResponse()

    monkeypatch.setattr("gold_research.fetch_twse.requests.get", fake_get)
    quote = fetch_realtime_quote("00635U")
    assert quote == RealtimeQuote(
        code="00635U",
        price=48.10,
        previous_close=48.07,
        quote_date="20260826",
        quote_time="13:30:00",
    )


def test_fetch_realtime_quote_falls_back_to_previous_close_when_unmatched(
    monkeypatch,
):
    # "z" (last trade) is blank when the security hasn't traded yet today.
    sample_payload = {
        "msgArray": [
            {"c": "00635U", "z": "", "y": "48.0700", "d": "20260826", "t": ""}
        ],
        "rtmessage": "OK",
    }

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return sample_payload

    monkeypatch.setattr(
        "gold_research.fetch_twse.requests.get",
        lambda url, params, headers, timeout: FakeResponse(),
    )
    quote = fetch_realtime_quote("00635U")
    assert quote.price == 48.07


def test_fetch_realtime_quote_falls_back_when_z_is_a_literal_dash(monkeypatch):
    # TWSE's real sentinel for "no trade matched yet" is the literal string
    # "-", not an empty string — confirmed against a live response. "-" is
    # truthy in Python, so a naive `row.get("z") or row.get("y")` fallback
    # would never actually trigger and float("-") would raise.
    sample_payload = {
        "msgArray": [
            {"c": "00635U", "z": "-", "y": "45.8000", "d": "20260902", "t": "-"}
        ],
        "rtmessage": "OK",
    }

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return sample_payload

    monkeypatch.setattr(
        "gold_research.fetch_twse.requests.get",
        lambda url, params, headers, timeout: FakeResponse(),
    )
    quote = fetch_realtime_quote("00635U")
    assert quote.price == 45.80


def test_fetch_realtime_quote_uses_bid_ask_midpoint_when_z_is_dash(monkeypatch):
    # Some low-volume TWSE securities never populate "z" (last matched
    # trade) through this endpoint even while actively trading — confirmed
    # live for 00635U on a day it had clearly moved (open/high/low all set,
    # bid/ask live) but z stayed "-" all session. Falling back straight to
    # yesterday's close there would silently show a frozen, wrong price;
    # the bid/ask midpoint tracks the real market far more closely.
    sample_payload = {
        "msgArray": [
            {
                "c": "00635U",
                "z": "-",
                "y": "45.8000",
                "a": "44.5600_44.5700_44.5800_44.5900_44.6000_",
                "b": "44.5400_44.5300_44.5200_44.5100_44.5000_",
                "d": "20260902",
                "t": "12:48:05",
            }
        ],
        "rtmessage": "OK",
    }

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return sample_payload

    monkeypatch.setattr(
        "gold_research.fetch_twse.requests.get",
        lambda url, params, headers, timeout: FakeResponse(),
    )
    quote = fetch_realtime_quote("00635U")
    assert quote.price == pytest.approx(44.55)


def test_fetch_realtime_quote_returns_none_on_bad_status(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"msgArray": [], "rtmessage": "MIS-200"}

    monkeypatch.setattr(
        "gold_research.fetch_twse.requests.get",
        lambda url, params, headers, timeout: FakeResponse(),
    )
    assert fetch_realtime_quote("00635U") is None


def test_fetch_realtime_quote_returns_none_on_network_error(monkeypatch):
    import requests

    def raise_connection_error(url, params, headers, timeout):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr("gold_research.fetch_twse.requests.get", raise_connection_error)
    assert fetch_realtime_quote("00635U") is None
