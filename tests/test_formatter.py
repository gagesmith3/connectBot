"""Slack block formatting: mrkdwn fixes and the 3000-char section limit."""

from __future__ import annotations

from src.connectbot.response_formatter import API_CATALOG, ResponseFormatter


def _texts(blocks: list) -> list[str]:
    return [block.text.text for block in blocks]


def test_markdown_bold_becomes_slack_bold() -> None:
    assert _texts(ResponseFormatter.format_chat("We're **70%** to goal")) == ["We're *70%* to goal"]


def test_empty_message_still_renders() -> None:
    assert _texts(ResponseFormatter.format_chat("   ")) == ["…"]


def test_long_message_splits_on_line_boundaries() -> None:
    line = "x" * 99
    message = "\n".join([line] * 70)  # ~7000 chars

    texts = _texts(ResponseFormatter.format_chat(message))

    assert len(texts) > 1
    assert all(len(t) <= 2900 for t in texts)
    assert "\n".join(texts) == message


def test_single_huge_line_is_hard_cut() -> None:
    texts = _texts(ResponseFormatter.format_chat("y" * 6000))
    assert [len(t) for t in texts] == [2900, 2900, 200]


def test_help_lists_every_catalog_entry() -> None:
    help_text = _texts(ResponseFormatter.format_help())[0]
    for name, desc in API_CATALOG:
        assert f"*{name}* — {desc}" in help_text
