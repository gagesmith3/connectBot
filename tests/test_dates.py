"""Date references in questions -> ISO data_date. Today is frozen at 2026-10-07 (Wed)."""

from __future__ import annotations

import pytest

from src.connectbot.orchestrator import _extract_data_date


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("heading yesterday", "2026-10-06"),
        ("sales 3 days ago", "2026-10-04"),
        ("sales 1 day ago", "2026-10-06"),
        ("heading last friday", "2026-10-02"),
        ("heading on monday", "2026-10-05"),
        ("heading last wednesday", "2026-09-30"),  # same weekday means a week back, never today
        ("heading on 7/14", "2026-07-14"),
        ("heading on 12/25", "2025-12-25"),  # a future date without a year means last year
        ("heading on 7/14/25", "2025-07-14"),
        ("heading on 7/14/2024", "2024-07-14"),
        ("heading on 13/40", None),
        ("heading on 2/30", None),
        ("heading for sp21-1", None),  # hyphens are head names, not dates
        ("heading today", None),
        ("heading", None),
    ],
)
def test_extract_data_date(text: str, expected: str | None) -> None:
    assert _extract_data_date(text) == expected
