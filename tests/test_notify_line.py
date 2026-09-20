from gold_research.notify_line import (
    build_prior_low_break_message,
    build_stage_change_message,
    check_and_notify,
    read_notify_state,
    send_line_broadcast,
    should_notify_stage_change,
    write_notify_state,
)


def test_should_notify_stage_change_true_on_first_tranche_with_no_precondition():
    assert should_notify_stage_change(
        previous_stage="觀察區（尚未回檔到位）",
        current_stage="第一筆 20%",
        stage_signals=None,
    )


def test_should_notify_stage_change_false_when_stage_unchanged():
    assert not should_notify_stage_change(
        previous_stage="第一筆 20%",
        current_stage="第一筆 20%",
        stage_signals=None,
    )


def test_should_notify_stage_change_false_for_non_actionable_observation_stage():
    assert not should_notify_stage_change(
        previous_stage=None,
        current_stage="觀察區（尚未回檔到位）",
        stage_signals=None,
    )


def test_should_notify_stage_change_requires_true_precondition_signal():
    stage = "加碼 20%（前提：趨勢沒壞）"
    assert should_notify_stage_change(
        previous_stage="第一筆 20%",
        current_stage=stage,
        stage_signals={"trend_intact": True},
    )
    assert not should_notify_stage_change(
        previous_stage="第一筆 20%",
        current_stage=stage,
        stage_signals={"trend_intact": False},
    )
    assert not should_notify_stage_change(
        previous_stage="第一筆 20%",
        current_stage=stage,
        stage_signals=None,
    )


def test_build_stage_change_message_includes_key_numbers():
    msg = build_stage_change_message("元大S&P黃金", "第一筆 20%", 3.5)
    assert "元大S&P黃金" in msg
    assert "3.5" in msg
    assert "第一筆 20%" in msg


def test_build_prior_low_break_message_includes_prices():
    msg = build_prior_low_break_message("元大S&P黃金", 44.81, 44.5)
    assert "44.81" in msg
    assert "44.5" in msg


def test_send_line_broadcast_returns_false_without_token(monkeypatch):
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    assert send_line_broadcast("test") is False


def test_send_line_broadcast_posts_with_token(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "fake-token")

    class FakeResponse:
        def raise_for_status(self):
            return None

    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr("gold_research.notify_line.requests.post", fake_post)
    assert send_line_broadcast("hello") is True
    assert captured["headers"]["Authorization"] == "Bearer fake-token"
    assert captured["json"]["messages"][0]["text"] == "hello"


def test_send_line_broadcast_returns_false_on_request_failure(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "fake-token")

    import requests

    def fake_post(url, headers, json, timeout):
        raise requests.RequestException("network error")

    monkeypatch.setattr("gold_research.notify_line.requests.post", fake_post)
    assert send_line_broadcast("hello") is False


def test_read_write_notify_state_roundtrip(tmp_path):
    path = tmp_path / "notify_state.json"
    assert read_notify_state(path) == {}
    state = {"00635U": {"stage": "第一筆 20%", "prior_low_price": 44.0}}
    write_notify_state(path, state)
    assert read_notify_state(path) == state


def test_read_notify_state_returns_empty_on_corrupt_file(tmp_path):
    path = tmp_path / "notify_state.json"
    path.write_text("not json", encoding="utf-8")
    assert read_notify_state(path) == {}


def test_check_and_notify_sends_and_updates_state_on_stage_transition(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "gold_research.notify_line.send_line_broadcast",
        lambda message: sent.append(message) or True,
    )
    targets = {
        "00635U": {
            "display_name": "元大S&P黃金",
            "latest": {"close": 46.0},
            "indicators": {
                "pullback_stage": {"stage": "第一筆 20%", "pullback_pct": 3.5},
                "stage_signals": None,
                "prior_low": {"price": 44.0},
            },
        }
    }
    new_state = check_and_notify(targets, previous_state={})
    assert len(sent) == 1
    assert "元大S&P黃金" in sent[0]
    assert new_state["00635U"]["stage"] == "第一筆 20%"
    assert new_state["00635U"]["prior_low_price"] == 44.0


def test_check_and_notify_skips_when_stage_unchanged(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "gold_research.notify_line.send_line_broadcast",
        lambda message: sent.append(message) or True,
    )
    targets = {
        "00635U": {
            "display_name": "元大S&P黃金",
            "latest": {"close": 46.0},
            "indicators": {
                "pullback_stage": {"stage": "第一筆 20%", "pullback_pct": 3.5},
                "stage_signals": None,
                "prior_low": {"price": 44.0},
            },
        }
    }
    previous_state = {"00635U": {"stage": "第一筆 20%", "prior_low_price": 44.0}}
    check_and_notify(targets, previous_state)
    assert sent == []


def test_check_and_notify_sends_prior_low_break_once(monkeypatch):
    # prior_swing_low() now auto-updates to a new, lower reference the
    # instant its old one gets breached — so "did it break?" is detected by
    # comparing this run's prior_low price against what was recorded last
    # time, not by comparing today's close against a price that, by the time
    # a breach happens, has already moved on to something new.
    sent = []
    monkeypatch.setattr(
        "gold_research.notify_line.send_line_broadcast",
        lambda message: sent.append(message) or True,
    )
    targets = {
        "00635U": {
            "display_name": "元大S&P黃金",
            "latest": {"close": 43.5},
            "indicators": {
                "pullback_stage": {
                    "stage": "最後 40%（等重新轉強）",
                    "pullback_pct": 10.0,
                },
                "stage_signals": {"macd_golden_cross": False},
                "prior_low": {"price": 43.0},  # the new, lower reference
            },
        }
    }
    previous_state = {
        "00635U": {"stage": "加碼 20%（前提：出現止跌）", "prior_low_price": 44.0}
    }
    new_state = check_and_notify(targets, previous_state)
    assert any("跌破" in m for m in sent)
    assert "44" in sent[0]  # names the level that just broke, not the new one
    assert new_state["00635U"]["prior_low_price"] == 43.0

    # A second run where the reference hasn't dropped further shouldn't
    # re-send the break alert.
    sent.clear()
    check_and_notify(targets, new_state)
    assert sent == []
