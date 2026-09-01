from datetime import UTC, datetime

from gold_research.fetch_macro import LatestQuote
from gold_research.fetch_twse import RealtimeQuote
from gold_research.update_quotes import build_live_quotes_payload


def test_build_live_quotes_payload_includes_twse_and_intl_gold_quotes():
    twse_realtime = {
        "00635U": RealtimeQuote(
            code="00635U",
            price=45.89,
            previous_close=45.5,
            quote_date="20260831",
            quote_time="13:30:00",
        ),
        "00708L": RealtimeQuote(
            code="00708L",
            price=82.70,
            previous_close=81.0,
            quote_date="20260831",
            quote_time="13:30:00",
        ),
    }
    intl_gold_realtime = LatestQuote(price=4479.5, quote_time="2026-08-31 23:35")

    payload = build_live_quotes_payload(
        twse_realtime, intl_gold_realtime, datetime(2026, 8, 31, 23, 35, tzinfo=UTC)
    )

    assert payload["quotes"]["00635U"] == {
        "close": 45.89,
        "quote_time": "13:30:00",
        "is_realtime": True,
    }
    assert payload["quotes"]["00708L"] == {
        "close": 82.70,
        "quote_time": "13:30:00",
        "is_realtime": True,
    }
    assert payload["quotes"]["GCF"] == {
        "close": 4479.5,
        "quote_time": "2026-08-31 23:35",
        "is_realtime": True,
    }
    assert payload["generated_at"]


def test_build_live_quotes_payload_skips_failed_fetches():
    twse_realtime = {"00635U": None, "00708L": None}
    payload = build_live_quotes_payload(
        twse_realtime, None, datetime(2026, 8, 31, 23, 35, tzinfo=UTC)
    )
    assert payload["quotes"] == {}
