import pytest

from gold_research.fetch_tbb_gold import (
    TbbGoldFetchError,
    parse_current_quote,
    parse_history,
)


def _current_page(records: str) -> str:
    return (
        "<script>var data = "
        '{"log":{},"msgCode":"FE0001","result":true,'
        f'"data":{{"REC":{{"data":[{records}]}},"SIZE":1}},"Detail":"N"}};'
        ' var holiday_flag = "0" if(true){}</script>'
    )


_RECORD = (
    '{"PRICE2":"12,657","PRICE1":"6,360","QTIME":"10:08:37",'
    '"LASTDATE":"20261117","LASTTIME":"101008","GOLDPRICEID":"1151117100837",'
    '"SPRICE":"4,080","QDATE":"115/11/17","BPRICE":"4,126"}'
)


def test_parse_current_quote_reads_buy_sell_and_listing_time():
    quote = parse_current_quote(_current_page(_RECORD))
    # BPRICE＝本行賣出價（你買進），SPRICE＝本行買進價（你賣回）。
    assert quote.sell == 4126.0
    assert quote.buy == 4080.0
    assert quote.listed_date == "2026-11-17"
    assert quote.listed_time == "10:10:08"


def test_parse_current_quote_returns_none_when_no_record_today():
    assert parse_current_quote(_current_page("")) is None


def test_parse_current_quote_raises_when_page_layout_changed():
    with pytest.raises(TbbGoldFetchError):
        parse_current_quote("<html>maintenance</html>")


def test_parse_current_quote_raises_when_record_fields_are_missing():
    with pytest.raises(TbbGoldFetchError):
        parse_current_quote(_current_page('{"BPRICE":"4,126"}'))


def _history_page(
    rows: list[tuple[str, str, str]],
    first: str = "本行買進價格",
    second: str = "本行賣出價格",
) -> str:
    body = "".join(
        f'<tr> <td class="text-center">{d}</td> '
        f'<td class="text-right">{a}</td> <td class="text-right">{b}</td> </tr>'
        for d, a, b in rows
    )
    return (
        "<table><thead><tr><th>日期</th>"
        f"<th>{first}</th><th>{second}</th></tr></thead><tbody>{body}</tbody></table>"
    )


def test_parse_history_returns_sorted_days_with_buy_and_sell():
    html = _history_page(
        [("2026/11/17", "4,080", "4,126"), ("2026/11/16", "4,060", "4,106")]
    )
    days = parse_history(html)
    assert [d.trading_date for d in days] == ["2026-11-16", "2026-11-17"]
    assert days[-1].buy == 4080.0
    assert days[-1].sell == 4126.0


def test_parse_history_refuses_swapped_columns():
    # 銀行若把欄位順序調換，買賣價會整個反過來，寧可失敗也不能顯示錯的價格。
    html = _history_page(
        [("2026/11/17", "4,126", "4,080")], first="本行賣出價格", second="本行買進價格"
    )
    with pytest.raises(TbbGoldFetchError):
        parse_history(html)


def test_parse_history_raises_when_headers_or_rows_missing():
    with pytest.raises(TbbGoldFetchError):
        parse_history("<table><tr><th>日期</th><th>其他</th></tr></table>")
    with pytest.raises(TbbGoldFetchError):
        parse_history(_history_page([]))
