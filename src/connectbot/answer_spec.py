"""
The answer spec: the JSON shape the LLM returns for a business answer.

The LLM picks a layout and writes the words (headline, body, insight
phrasing). For KPIs and tables it only *names fields*; answer_renderer pulls
the values from the FastAPI evidence, so the model never retypes a number
that ends up in a table. parse_answer_spec drops anything that doesn't
resolve against the evidence it was given.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

LAYOUTS = ("quick", "table", "rundown", "prose")
FORMATS = ("int", "qty", "pct", "usd", "date", "text")

MAX_KPIS = 6
MAX_TABLE_ROWS = 15
MAX_SECTIONS = 5
MAX_INSIGHTS = 2
_MAX_TEXT = 2500

# Embedded in the business and tool-router prompts.
JSON_SCHEMA_PROMPT = """Reply with ONE JSON object and nothing else:
{
  "layout": "quick" | "table" | "rundown" | "prose",
  "headline": "one short line that answers the question, with the key number",
  "body": "optional, ONE short sentence of context the kpis don't show",
  "kpis": [{"source": "<tool>", "field": "<field>", "label": "Short label", "format": "qty"}],
  "table": {"source": "<tool>", "rows": "rows", "columns": [{"field": "<row field>", "label": "Header", "format": "int"}],
            "sort": "-<row field>", "limit": 10},
  "sections": [{"title": "Heading", "headline": "...", "body": "...", "kpis": [...], "table": {...}}],
  "insights": [{"id": "<computed fact id>", "text": "your phrasing of that fact"}]
}
Choosing a layout:
- quick: status / single-number questions ("how's heading today?") — headline + 2-4 kpis.
- table: lists, rankings, breakdowns, comparisons across rows — headline + table.
- rundown: several topics at once (morning rundown) — one section per topic.
- prose: explanations or anything that doesn't fit the others — headline + body.
Rules:
- "source" is the tool / endpoint name the data came from; omit it when there is only one.
- "field" names a key in that data; use dots for nesting (e.g. "daily.sales_total").
- Table "rows" is the key holding the row list (usually "rows"); columns name keys inside each row.
- Never put numbers in kpis/table yourself — only field names. Numbers in headline/body must come from the evidence.
- format: int (1,234), qty (1.2M), pct (value already in percent), usd, date, text.
- Don't repeat yourself: the body must not restate a kpi, and an insight must add something the
  headline, body and kpis don't already say. Leave body/insights out rather than repeat.
- insights: 0-2 items, ONLY ids from "Computed facts"; skip if none are worth saying.
- Slack mrkdwn in text fields: *single asterisks* for bold, no markdown tables or headings."""

_MISSING = object()


@dataclass
class Kpi:
    source: str
    field: str
    label: str
    format: str


@dataclass
class Column:
    field: str
    label: str
    format: str


@dataclass
class Table:
    source: str
    rows_path: str
    columns: list[Column]
    sort: str | None
    limit: int


@dataclass
class Section:
    title: str
    headline: str
    body: str = ""
    kpis: list[Kpi] = field(default_factory=list)
    table: Table | None = None


@dataclass
class Insight:
    id: str
    text: str


@dataclass
class AnswerSpec:
    layout: str
    headline: str
    body: str = ""
    kpis: list[Kpi] = field(default_factory=list)
    table: Table | None = None
    sections: list[Section] = field(default_factory=list)
    insights: list[Insight] = field(default_factory=list)


def resolve_path(data: Any, path: str) -> Any:
    """Follow a dotted path through nested dicts; _MISSING if any step is absent."""
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def is_missing(value: Any) -> bool:
    return value is _MISSING


def table_rows(evidence: dict[str, Any], table: Table) -> list[dict[str, Any]]:
    rows = resolve_path(evidence.get(table.source), table.rows_path)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _extract_json(raw: str) -> dict[str, Any] | None:
    text = re.sub(r"```(?:json)?", "", raw or "")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _text(value: Any, limit: int = _MAX_TEXT) -> str:
    return str(value).strip()[:limit] if isinstance(value, str | int | float) else ""


def _format(value: Any) -> str:
    return value if value in FORMATS else "text"


def _source(raw: dict[str, Any], evidence: dict[str, Any]) -> str | None:
    source = raw.get("source")
    if source is None and len(evidence) == 1:
        return next(iter(evidence))
    return source if isinstance(source, str) and source in evidence else None


def _parse_kpis(raw: Any, evidence: dict[str, Any]) -> list[Kpi]:
    kpis: list[Kpi] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        source = _source(item, evidence)
        path = item.get("field")
        if source is None or not isinstance(path, str) or is_missing(resolve_path(evidence[source], path)):
            continue
        label = _text(item.get("label"), 40) or path.rsplit(".", 1)[-1].replace("_", " ")
        kpis.append(Kpi(source=source, field=path, label=label, format=_format(item.get("format"))))
    return kpis[:MAX_KPIS]


def _parse_table(raw: Any, evidence: dict[str, Any]) -> Table | None:
    if not isinstance(raw, dict):
        return None
    source = _source(raw, evidence)
    if source is None:
        return None
    rows_raw = raw.get("rows")
    rows_path = rows_raw if isinstance(rows_raw, str) else "rows"
    table = Table(source=source, rows_path=rows_path, columns=[], sort=None, limit=MAX_TABLE_ROWS)
    rows = table_rows(evidence, table)
    if not rows:
        return None
    known = set(rows[0])
    for column in raw.get("columns") or []:
        if isinstance(column, dict) and column.get("field") in known:
            name = column["field"]
            label = _text(column.get("label"), 24) or name.replace("_", " ")
            table.columns.append(Column(field=name, label=label, format=_format(column.get("format"))))
    if not table.columns:
        return None
    sort = raw.get("sort")
    if isinstance(sort, str) and sort.lstrip("-") in known:
        table.sort = sort
    try:
        table.limit = max(1, min(int(raw.get("limit") or MAX_TABLE_ROWS), MAX_TABLE_ROWS))
    except (TypeError, ValueError):
        table.limit = MAX_TABLE_ROWS
    return table


def _parse_sections(raw: Any, evidence: dict[str, Any]) -> list[Section]:
    sections: list[Section] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        section = Section(
            title=_text(item.get("title"), 80),
            headline=_text(item.get("headline"), 300),
            body=_text(item.get("body"), 800),
            kpis=_parse_kpis(item.get("kpis"), evidence),
            table=_parse_table(item.get("table"), evidence),
        )
        if section.headline or section.body or section.kpis or section.table:
            sections.append(section)
    return sections[:MAX_SECTIONS]


def parse_answer_spec(
    raw: str,
    evidence: dict[str, Any],
    fact_ids: set[str] | None = None,
) -> AnswerSpec | None:
    """Parse and validate the LLM's JSON answer. None means "not a usable spec"
    and the caller renders the raw reply as prose, as before specs existed."""
    data = _extract_json(raw)
    if data is None:
        return None
    headline = _text(data.get("headline"), 300)
    body = _text(data.get("body"))
    if not headline and not body:
        return None

    layout_raw = data.get("layout")
    layout = layout_raw if isinstance(layout_raw, str) and layout_raw in LAYOUTS else "prose"
    spec = AnswerSpec(
        layout=layout,
        headline=headline,
        body=body,
        kpis=_parse_kpis(data.get("kpis"), evidence),
        table=_parse_table(data.get("table"), evidence),
        sections=_parse_sections(data.get("sections"), evidence),
    )
    allowed = fact_ids or set()
    for item in data.get("insights") or []:
        if isinstance(item, dict) and item.get("id") in allowed and _text(item.get("text"), 300):
            spec.insights.append(Insight(id=item["id"], text=_text(item["text"], 300)))
    spec.insights = spec.insights[:MAX_INSIGHTS]

    # A layout whose structure didn't survive validation degrades to quick.
    if (spec.layout == "table" and spec.table is None) or (spec.layout == "rundown" and not spec.sections):
        spec.layout = "quick"
    return spec
