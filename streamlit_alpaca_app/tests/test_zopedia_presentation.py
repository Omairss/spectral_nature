from __future__ import annotations

from services.zopedia_presentation import (
    parse_markdown_table,
    prepare_answer_markdown_blocks,
    trace_step_body,
    trace_step_title,
)


def test_prepare_answer_blocks_preserves_heading_followed_by_table():
    answer = """**Representative stocks rose despite higher yields.**

### Stock performance (May 22 close) | Ticker | Sector
| Close | Change % |
|--------|--------|--------|
| AAPL | Tech/Growth | 308.81 | +1.23% |
| JPM | Financials | 306.34 | +1.11% |
"""

    blocks = prepare_answer_markdown_blocks(answer)

    assert blocks[0] == "**Representative stocks rose despite higher yields.**"
    assert blocks[1] == "### Stock performance (May 22 close)"
    table = parse_markdown_table(blocks[2])
    assert table is not None
    assert table.columns == ["Ticker", "Sector", "Close", "Change %"]
    assert table.rows[0] == {
        "Ticker": "AAPL",
        "Sector": "Tech/Growth",
        "Close": "308.81",
        "Change %": "+1.23%",
    }


def test_parse_markdown_table_repairs_one_line_pipe_table():
    table = parse_markdown_table(
        "| Ticker | Sector | Close | Change % | | --- | --- | --- | --- | "
        "| AAPL | Tech/Growth | 308.81 | +1.23% | | NEE | Utilities | 88.54 | -1.28% |"
    )

    assert table is not None
    assert table.columns == ["Ticker", "Sector", "Close", "Change %"]
    assert table.rows == [
        {"Ticker": "AAPL", "Sector": "Tech/Growth", "Close": "308.81", "Change %": "+1.23%"},
        {"Ticker": "NEE", "Sector": "Utilities", "Close": "88.54", "Change %": "-1.28%"},
    ]


def test_trace_titles_keep_visible_planner_content_polished():
    reasoning = {"type": "reasoning", "text": "Need to identify representative stocks before synthesis."}
    tool = {"type": "tool_start", "tool_name": "dataset.daily_movers", "args_text": '{"symbols": ["AAPL"]}'}

    assert trace_step_title(reasoning, index=1) == "1. Reasoning"
    assert trace_step_body(reasoning) == "Need to identify representative stocks before synthesis."
    assert trace_step_title(tool, index=2, tool_label="daily movers") == "2. Checking daily movers"
    assert trace_step_body(tool) == 'Arguments: {"symbols": ["AAPL"]}'
