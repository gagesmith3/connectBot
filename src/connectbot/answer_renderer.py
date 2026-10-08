"""
Render an AnswerSpec into Slack blocks.

Every number in a KPI or table is read from the FastAPI evidence here; the
spec only says which fields to show and how to format them.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from slack_sdk.models.blocks import Block, ContextBlock, DividerBlock, SectionBlock
from slack_sdk.models.blocks.block_elements import MarkdownTextObject

from .answer_spec import AnswerSpec, Column, Kpi, Table, is_missing, resolve_path, table_rows
from .response_formatter import _MD_BOLD_RE, _split_sections

# Monospace width that still renders unwrapped in Slack on a phone.
TABLE_WIDTH = 44
_MIN_TEXT_COL = 6
_GAP = "  "
_NUMERIC_FORMATS = {"int", "qty", "pct", "usd"}
_SLACK_MAX_BLOCKS = 50
_MAX_FIELDS = 10


def format_value(value: Any, fmt: str) -> str:
    if value is None:
        return "—"
    if fmt == "date":
        parsed = _parse_when(value)
        return _short_date(parsed) if parsed else str(value)
    if fmt in _NUMERIC_FORMATS:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if fmt == "pct":
            return f"{_half_up(number, '1')}%"
        if fmt == "usd":
            return f"${number:,.0f}"
        if fmt == "qty":
            if abs(number) >= 1_000_000:
                return f"{_half_up(number / 1_000_000, '0.01')}M"
            if abs(number) >= 10_000:
                return f"{_half_up(number / 1_000, '1')}K"
        return f"{_half_up(number, '1'):,}"
    return str(value)


def _half_up(number: float, step: str) -> Decimal:
    """Round like a person would: 3.855 -> 3.86 and 2.5 -> 3 (float formatting
    gives 3.85 and round() gives 2)."""
    return Decimal(str(number)).quantize(Decimal(step), rounding=ROUND_HALF_UP)


def _parse_when(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _short_date(when: date) -> str:
    label = f"{when:%b} {when.day}"
    return label if when.year == date.today().year else f"{label}, {when.year}"


def _short_time(when: datetime) -> str:
    hour = when.hour % 12 or 12
    return f"{hour}:{when.minute:02d} {'AM' if when.hour < 12 else 'PM'}"


_TIMESTAMP_KEYS = ("snapshot_ts", "updated_at")
_DATE_KEYS = ("data_date", "end_date")


def freshness_label(payload: Any) -> str:
    """'as of Oct 7, 2:30 PM' from a snapshot/update timestamp, else 'for Oct 6'
    from a data date. Looks at the top level, then one level down (nested
    payloads like sales' daily/summary, or the first row)."""
    candidates: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        candidates.append(payload)
        candidates.extend(v for v in payload.values() if isinstance(v, dict))
        rows = payload.get("rows")
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            candidates.append(rows[0])
    for keys, as_timestamp in ((_TIMESTAMP_KEYS, True), (_DATE_KEYS, False)):
        for candidate in candidates:
            for key in keys:
                when = _parse_when(candidate.get(key))
                if when is None:
                    continue
                if as_timestamp and len(str(candidate[key])) > 10:
                    return f"as of {_short_date(when)}, {_short_time(when)}"
                return f"for {_short_date(when)}"
    return ""


def _source_label(source: str) -> str:
    return source.split("#", 1)[0].replace("_", " ")


def _footer(evidence: dict[str, Any]) -> ContextBlock:
    parts: dict[str, None] = {}  # ordered set: repeated calls of one tool share a label
    for source, payload in evidence.items():
        fresh = freshness_label(payload)
        parts[f"{_source_label(source)} · {fresh}" if fresh else _source_label(source)] = None
    return ContextBlock(elements=[MarkdownTextObject(text="  |  ".join(parts))])


def _clean(text: str) -> str:
    return _MD_BOLD_RE.sub(r"*\1*", text).strip()


def _headline_block(headline: str) -> SectionBlock:
    return SectionBlock(text=f"*{headline.replace('*', '').strip()}*")


def _kpi_block(kpis: list[Kpi], evidence: dict[str, Any]) -> SectionBlock:
    fields = []
    for kpi in kpis[:_MAX_FIELDS]:
        value = resolve_path(evidence.get(kpi.source), kpi.field)
        shown = "—" if is_missing(value) else format_value(value, kpi.format)
        fields.append(MarkdownTextObject(text=f"*{kpi.label}*\n{shown}"))
    return SectionBlock(fields=fields)


def _sort_key(field: str) -> Any:
    def key(row: dict[str, Any]) -> tuple[int, Any]:
        value = row.get(field)
        if isinstance(value, int | float):
            return (0, value)
        return (1, str(value or ""))

    return key


def _fit_widths(columns: list[Column], widths: list[int]) -> list[int]:
    widths = list(widths)
    total = sum(widths) + len(_GAP) * (len(widths) - 1)
    while total > TABLE_WIDTH:
        text_cols = [i for i, c in enumerate(columns) if c.format not in _NUMERIC_FORMATS and widths[i] > _MIN_TEXT_COL]
        if not text_cols:
            break
        widest = max(text_cols, key=lambda i: widths[i])
        widths[widest] -= 1
        total -= 1
    return widths


def _cell(text: str, width: int, numeric: bool) -> str:
    if len(text) > width:
        text = text[: width - 1] + "…"
    return text.rjust(width) if numeric else text.ljust(width)


def _table_blocks(table: Table, evidence: dict[str, Any]) -> list[Block]:
    rows = table_rows(evidence, table)
    if table.sort:
        field = table.sort.lstrip("-")
        numeric = [r for r in rows if isinstance(r.get(field), int | float)]
        other = [r for r in rows if not isinstance(r.get(field), int | float)]
        rows = sorted(numeric, key=_sort_key(field), reverse=table.sort.startswith("-")) + other
    shown, hidden = rows[: table.limit], max(0, len(rows) - table.limit)

    cells = [[format_value(row.get(c.field), c.format) for c in table.columns] for row in shown]
    widths = [max([len(c.label)] + [len(r[i]) for r in cells]) for i, c in enumerate(table.columns)]
    widths = _fit_widths(table.columns, widths)
    numeric_cols = [c.format in _NUMERIC_FORMATS for c in table.columns]

    def line(values: list[str]) -> str:
        return _GAP.join(_cell(v, w, n) for v, w, n in zip(values, widths, numeric_cols, strict=True)).rstrip()

    lines = [line([c.label for c in table.columns]), "-" * (sum(widths) + len(_GAP) * (len(widths) - 1))]
    lines += [line(r) for r in cells]
    blocks: list[Block] = [SectionBlock(text="```" + "\n".join(lines) + "```")]
    if hidden:
        blocks.append(ContextBlock(elements=[MarkdownTextObject(text=f"_…and {hidden} more_")]))
    return blocks


def _body_blocks(body: str) -> list[Block]:
    return list(_split_sections(body)) if body.strip() else []


def render(
    spec: AnswerSpec, evidence: dict[str, Any], severities: dict[str, str] | None = None
) -> tuple[str, list[Block]]:
    """Return (plain fallback text, Slack blocks). `severities` maps fact id to
    info/warn/good; warn insights get a warning marker."""
    blocks: list[Block] = []
    text_lines: list[str] = []
    if spec.headline:
        blocks.append(_headline_block(spec.headline))
        text_lines.append(spec.headline.replace("*", ""))

    if spec.layout == "rundown":
        for section in spec.sections:
            blocks.append(DividerBlock())
            title = f"*{section.title.replace('*', '')}*" if section.title else ""
            intro = "\n".join(part for part in (title, _clean(section.headline)) if part)
            if intro:
                blocks.append(SectionBlock(text=intro))
            if section.kpis:
                blocks.append(_kpi_block(section.kpis, evidence))
            blocks.extend(_body_blocks(_clean(section.body)))
            if section.table:
                blocks.extend(_table_blocks(section.table, evidence))
            summary = ": ".join(part for part in (section.title, section.headline) if part)
            if summary:
                text_lines.append(summary.replace("*", ""))
    else:
        if spec.kpis and spec.layout != "prose":
            blocks.append(_kpi_block(spec.kpis, evidence))
        blocks.extend(_body_blocks(_clean(spec.body)))
        if spec.table and spec.layout == "table":
            blocks.extend(_table_blocks(spec.table, evidence))
        if spec.body:
            text_lines.append(spec.body)

    if spec.insights:
        marks = severities or {}
        lines = [f"{':warning:' if marks.get(i.id) == 'warn' else ':bulb:'} {_clean(i.text)}" for i in spec.insights]
        blocks.append(SectionBlock(text="\n".join(lines)))
        text_lines.extend(i.text for i in spec.insights)

    blocks = blocks[: _SLACK_MAX_BLOCKS - 1]
    if evidence:
        blocks.append(_footer(evidence))
    return "\n".join(text_lines), blocks
