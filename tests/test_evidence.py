"""_trim_evidence keeps LLM prompts small: newest rows only, at most 80."""

from __future__ import annotations

from src.connectbot.orchestrator import ConnectBotOrchestrator

trim = ConnectBotOrchestrator._trim_evidence


def _heading_rows(days: int, per_day: int) -> list[dict[str, str]]:
    # newest first, like the API
    return [{"data_date": f"2026-10-{30 - d:02d}", "head": str(h)} for d in range(days) for h in range(per_day)]


def test_small_payload_is_untouched() -> None:
    data = {"rows": [{"a": 1}] * 80}
    assert trim("backlog_lots", data) is data


def test_heading_keeps_seven_newest_dates() -> None:
    data = {"count": 100, "rows": _heading_rows(days=10, per_day=10)}

    trimmed = trim("heading", data)

    dates = {row["data_date"] for row in trimmed["rows"]}
    assert dates == {f"2026-10-{30 - d:02d}" for d in range(7)}
    assert len(trimmed["rows"]) == 70
    assert trimmed["count"] == 100
    assert "70 most recent of 100" in trimmed["evidence_note"]


def test_heading_never_exceeds_eighty_rows() -> None:
    trimmed = trim("heading", {"rows": _heading_rows(days=10, per_day=20)})
    assert len(trimmed["rows"]) == 80


def test_other_endpoints_keep_first_eighty_rows() -> None:
    rows = [{"i": i} for i in range(200)]
    trimmed = trim("backlog_lots", {"rows": rows})
    assert trimmed["rows"] == rows[:80]
