import pytest

from gold_research.fetch_twse import DailyBar, TwseFetchError, _parse_row, fetch_month


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
