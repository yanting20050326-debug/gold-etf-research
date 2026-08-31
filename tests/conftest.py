import pytest


@pytest.fixture(autouse=True)
def _no_real_gemini_key_by_default(monkeypatch):
    """預設移除 GEMINI_API_KEY，避免測試不小心打到真實 Gemini API。

    真的想測「有 key」行為的測試（見 test_llm_summary.py）會自己用
    monkeypatch.setenv 覆蓋回去，在該測試內生效；這個 fixture 只負責
    「預設沒有 key」這個安全基準，不擋任何測試主動要模擬的情境。
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
