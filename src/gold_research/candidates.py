"""研究參考用的候選標的清單。

這些標的**沒有**接自動化技術面資料管線——00708L/00674R 是槓桿/反向，
黃金存摺跟海外 ETF 則沒有穩定的公開歷史資料 API。純文字說明＋來源連結，
不假裝是正式可交易訊號。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    as_of: str


@dataclass(frozen=True)
class Candidate:
    code: str
    name: str
    type_: str
    note: str
    source: Source


CANDIDATES: list[Candidate] = [
    Candidate(
        code="00708L",
        name="期元大S&P黃金正2",
        type_="槓桿期貨ETF（2倍）",
        note="僅適合短線操作，槓桿長期持有會偏離原型指數，不建議定期定額。",
        source=Source(
            name="TWSE ETFortune",
            url="https://www.twse.com.tw/zh/ETFortune/etfInfo/00708L",
            as_of="2026-08-20",
        ),
    ),
    Candidate(
        code="00674R",
        name="期元大S&P黃金反1",
        type_="反向期貨ETF",
        note="做空黃金期貨的短線避險工具，不適合長期持有。",
        source=Source(
            name="TWSE ETFortune",
            url="https://www.twse.com.tw/zh/ETFortune/etfInfo/00674R",
            as_of="2026-08-20",
        ),
    ),
    Candidate(
        code="gold_passbook",
        name="台灣銀行／兆豐銀行黃金存摺",
        type_="銀行黃金存摺（非ETF）",
        note="銀行自報牌價，買賣價差是隱藏成本；沒有公開歷史資料 API，無法自動化技術分析。",
        source=Source(
            name="台灣銀行", url="https://rate.bot.com.tw/gold", as_of="2026-08-20"
        ),
    ),
    Candidate(
        code="GLD",
        name="SPDR Gold Shares",
        type_="海外實體黃金ETF",
        note="需透過複委託或海外券商買賣；價差獲利屬海外所得，實際課稅規則請洽國稅局或稅務顧問確認。",
        source=Source(
            name="State Street SPDR",
            url="https://www.ssga.com/us/en/individual/etfs/spdr-gold-shares-gld",
            as_of="2026-08-20",
        ),
    ),
    Candidate(
        code="IAU",
        name="iShares Gold Trust",
        type_="海外實體黃金ETF",
        note="管理費較 GLD 低，同樣需複委託／海外券商，海外所得課稅考量同上。",
        source=Source(
            name="iShares (BlackRock)",
            url="https://www.ishares.com/us/products/239561/ishares-gold-trust-fund",
            as_of="2026-08-20",
        ),
    ),
]
