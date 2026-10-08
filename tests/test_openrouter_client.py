from src.connectbot.openrouter_client import _strip_reasoning


def test_plain_answer_unchanged() -> None:
    assert _strip_reasoning("Heading is at 87% of goal.") == "Heading is at 87% of goal."


def test_think_block_removed() -> None:
    assert _strip_reasoning("<think>check the rows</think>Backlog is 1,200 lots.") == "Backlog is 1,200 lots."


def test_final_marker_removed() -> None:
    assert _strip_reasoning("finalWe're at 87% of today's goal.") == "We're at 87% of today's goal."


def test_leaked_analysis_channel_without_final_is_unusable() -> None:
    # gpt-oss harmony leak seen in the 2026-10-07 model benchmark: reasoning
    # plus a tool call written as text, no final answer.
    leaked = (
        "analysisWe need yesterday's studs for National 2 head. The tool heading returned no data. "
        'Let\'s call heading without date.assistantcommentary to=functions.heading json{"head_name":"NATIONAL_2"}'
    )
    assert _strip_reasoning(leaked) == ""


def test_leaked_analysis_channel_keeps_final_answer() -> None:
    leaked = "analysisUser wants backlog. Rows say 1,200.assistantfinalBacklog is 1,200 lots."
    assert _strip_reasoning(leaked) == "Backlog is 1,200 lots."


def test_answer_starting_with_word_analysis_unchanged() -> None:
    assert _strip_reasoning("Analysis of the rows: backlog is flat.") == "Analysis of the rows: backlog is flat."


def test_tool_calls_written_as_text_are_unusable() -> None:
    # glm-5.3-flash after a gpt-6-luna 400 (2026-10-07): tool calls as markup, no answer.
    leaked = (
        "<tool_call>heading<arg_key>data_date</arg_key><arg_value></arg_value>"
        "<arg_key>head_name</arg_key><arg_value>NATIONAL_2</arg_value></tool_call>"
    )
    assert _strip_reasoning(leaked) == ""
    assert _strip_reasoning("Backlog is 5 lots." + leaked) == "Backlog is 5 lots."
