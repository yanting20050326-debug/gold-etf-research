from datetime import date

from gold_research.health_check import check_payload


def _valid_payload(as_of: str = "2026-08-20") -> dict:
    return {
        "disclaimer": "...",
        "targets": {"00635U": {"data_source": {"as_of": as_of}}},
        "macro_context": {"data_source": {"as_of": as_of}, "stale": False},
    }


def test_check_payload_passes_for_fresh_valid_data():
    issues = check_payload(_valid_payload(), today=date(2026, 8, 20))
    assert issues == []


def test_check_payload_flags_missing_top_level_key():
    payload = _valid_payload()
    del payload["macro_context"]
    issues = check_payload(payload, today=date(2026, 8, 20))
    assert any(i.severity == "error" and "macro_context" in i.message for i in issues)


def test_check_payload_flags_missing_target():
    payload = _valid_payload()
    payload["targets"] = {}
    issues = check_payload(payload, today=date(2026, 8, 20))
    assert any(i.severity == "error" and "00635U" in i.message for i in issues)


def test_check_payload_warns_on_stale_data():
    issues = check_payload(_valid_payload(as_of="2026-08-01"), today=date(2026, 8, 20))
    assert any(i.severity == "warning" and "00635U" in i.message for i in issues)


def test_check_payload_warns_when_macro_context_is_stale_flagged():
    payload = _valid_payload()
    payload["macro_context"]["stale"] = True
    issues = check_payload(payload, today=date(2026, 8, 20))
    assert any(i.severity == "warning" and "快取" in i.message for i in issues)


def test_check_payload_checks_staleness_for_every_target():
    payload = _valid_payload()
    payload["targets"]["00708L"] = {"data_source": {"as_of": "2026-08-01"}}
    issues = check_payload(payload, today=date(2026, 8, 20))
    assert any(i.severity == "warning" and "00708L" in i.message for i in issues)
