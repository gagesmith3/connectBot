"""
Format API responses into human-readable Slack messages
"""

from __future__ import annotations

import re
from typing import Any

from slack_sdk.models.blocks import Block, ContextBlock, DividerBlock, SectionBlock
from slack_sdk.models.blocks.block_elements import MarkdownTextObject

# Slack rejects section blocks whose text exceeds 3000 characters.
_SECTION_TEXT_LIMIT = 2900

# Single source of truth for the data the bot is wired to. Update this list
# (plus the routing keywords in orchestrator/intent_parser and an api_client
# method) whenever a connectFastAPI endpoint is added or removed — the help
# and "what can you do" replies are built from it.
API_CATALOG = [
    ("Backlog", "current snapshot, plus breakdowns by stud size/length/material/customer and per-lot detail"),
    ("Heading", "plan-vs-actual production — overall, per head, or WTD/MTD/YTD totals"),
    ("Sales & quotes", "daily Sage totals, top items, day-over-day trend, and multi-day series"),
    (
        "Equipment",
        "machines/guns sold by model (week/month/YTD units), finished stock on hand, open build requests, low-stock build parts, and open/closed repair tickets",
    ),
    ("Wire inventory", "spool levels by location (shed/head/farm)"),
    ("Wire usage", "consumption over 30/90/180/365 days"),
    ("Wire demand", "4-week demand vs stock and shortages"),
    ("Data pipeline", "compute job health and freshness"),
]


def capability_summary() -> str:
    """One-line list of API_CATALOG for replies: "backlog, heading, ..., or data pipeline"."""
    names = [name.lower() for name, _ in API_CATALOG]
    return ", ".join(names[:-1]) + f", or {names[-1]}"


# LLMs slip into markdown bold despite prompting; Slack mrkdwn needs *single*.
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _split_sections(message: str) -> list[SectionBlock]:
    """Split long text into multiple SectionBlocks at line boundaries."""
    message = _MD_BOLD_RE.sub(r"*\1*", message).strip() or "…"
    if len(message) <= _SECTION_TEXT_LIMIT:
        return [SectionBlock(text=message)]

    blocks: list[SectionBlock] = []
    chunk_lines: list[str] = []
    chunk_len = 0
    for line in message.split("\n"):
        # A single pathological line longer than the limit gets hard-cut.
        while len(line) > _SECTION_TEXT_LIMIT:
            if chunk_lines:
                blocks.append(SectionBlock(text="\n".join(chunk_lines)))
                chunk_lines, chunk_len = [], 0
            blocks.append(SectionBlock(text=line[:_SECTION_TEXT_LIMIT]))
            line = line[_SECTION_TEXT_LIMIT:]
        if chunk_len + len(line) + 1 > _SECTION_TEXT_LIMIT:
            blocks.append(SectionBlock(text="\n".join(chunk_lines)))
            chunk_lines, chunk_len = [], 0
        chunk_lines.append(line)
        chunk_len += len(line) + 1
    if chunk_lines and "\n".join(chunk_lines).strip():
        blocks.append(SectionBlock(text="\n".join(chunk_lines)))
    return blocks


class ResponseFormatter:
    """Format API responses for Slack"""

    @staticmethod
    def format_loading(frame: str = "...") -> list[Block]:
        return [
            SectionBlock(text=frame),
        ]

    @staticmethod
    def to_text(endpoint: str, data: dict[str, Any] | None = None) -> str:
        if endpoint == "chat" and data:
            return data.get("message", "I’m here.")

        if endpoint == "backlog" and data:
            pairs = []
            for key, value in data.items():
                if key not in ["id", "created_at", "snapshot_time"]:
                    pairs.append(f"{key.replace('_', ' ')}: {value}")
            return "Current backlog status\n" + "\n".join(pairs[:10])

        if endpoint == "heading" and data:
            return f"Heading metrics: found {data.get('count', 0)} record(s)."

        if endpoint == "heading_overall" and data:
            return (
                f"Heading plan-vs-actual for {data.get('data_date', 'today')}: "
                f"{data.get('actual_studs', 0):,} of {data.get('plan_studs', 0):,} studs "
                f"({data.get('volume_pct', 'n/a')}% of plan), "
                f"{data.get('running_headers_count', 0)} headers running."
            )

        if endpoint == "livewire_demand" and data:
            rows = data.get("rows", [])
            if not rows:
                material_query = data.get("material_query")
                if material_query:
                    return f"No livewire demand rows matched '{material_query}'."
                return "No livewire demand rows in the latest snapshot."

            shortage_rows = [
                row for row in rows if bool(row.get("shortage_flag")) or float(row.get("delta_lbs") or 0) < 0
            ]
            total_shortage_lbs = round(
                sum(abs(float(row.get("delta_lbs") or 0.0)) for row in shortage_rows),
                2,
            )
            snapshot = rows[0].get("snapshot_ts", "latest")
            return (
                f"Livewire demand snapshot {snapshot}: {len(shortage_rows)} shortage row(s), "
                f"{total_shortage_lbs} lbs total deficit."
            )

        if endpoint == "livewire_inventory" and data:
            rows = data.get("rows", [])
            if not rows:
                return "No spool inventory rows in the latest snapshot."
            total_weight = round(sum(float(r.get("weight_lbs") or 0) for r in rows), 2)
            total_spools = sum(int(r.get("spool_count") or 0) for r in rows)
            location = data.get("location") or "all locations"
            return (
                f"Wire inventory ({location}): {len(rows)} material/dia/owner group(s), "
                f"{total_spools} spool(s), {total_weight:,} lbs total."
            )

        if endpoint == "livewire_usage" and data:
            rows = data.get("rows", [])
            if not rows:
                return "No wire usage rows in the latest snapshot."
            total_30d = round(
                sum(float(r.get("used_lbs_30d") or 0) for r in rows if r.get("dimension") == rows[0].get("dimension")),
                2,
            )
            return (
                f"Wire usage: {data.get('count', 0)} row(s); "
                f"~{total_30d:,} lbs consumed in the last 30 days ({rows[0].get('dimension', 'n/a')} dimension)."
            )

        if endpoint == "help":
            return f"Ask about {capability_summary()}."

        return "Request processed."

    @staticmethod
    def format_chat(message: str) -> list[Block]:
        return list(_split_sections(message))

    @staticmethod
    def format_grounded_answer(
        message: str,
        endpoint: str | None = None,
        evidence_lines: list[str] | None = None,
    ) -> list[Block]:
        blocks: list[Block] = list(_split_sections(message))
        if evidence_lines:
            evidence_text = "\n".join(f"• {line}" for line in evidence_lines if line)
            if evidence_text:
                blocks.append(SectionBlock(text=f"*Evidence used*\n{evidence_text}"))
        if endpoint:
            blocks.append(SectionBlock(text=f"_Grounded with FastAPI endpoint: {endpoint}_"))
        return blocks

    @staticmethod
    def format_backlog(data: dict[str, Any]) -> list[Block]:
        """Format backlog snapshot response"""
        blocks: list[Block] = []

        if not data:
            blocks.append(SectionBlock(text="No backlog data available"))
            return blocks

        # Create a formatted text summary
        summary = "```\n"
        for key, value in data.items():
            if key not in ["id", "created_at", "snapshot_time"]:
                summary += f"{key.replace('_', ' ').title():<25}: {value}\n"
        summary += "```"

        blocks.append(SectionBlock(text=summary))
        return blocks

    @staticmethod
    def format_heading(data: dict[str, Any]) -> list[Block]:
        """Format heading metrics response"""
        blocks: list[Block] = []

        if not data or data.get("count", 0) == 0:
            blocks.append(SectionBlock(text="No heading data available"))
            return blocks

        count = data.get("count", 0)
        rows = data.get("rows", [])

        blocks.append(SectionBlock(text=f"Found {count} heading record(s)"))

        # Show first 5 rows as formatted text
        for row in rows[:5]:
            row_text = "```\n"
            for key, value in row.items():
                row_text += f"{key.replace('_', ' ').title():<20}: {value}\n"
            row_text += "```"
            blocks.append(SectionBlock(text=row_text))

        if count > 5:
            blocks.append(SectionBlock(text=f"_...and {count - 5} more records_"))

        return blocks

    @staticmethod
    def format_livewire_demand(data: dict[str, Any]) -> list[Block]:
        blocks: list[Block] = []
        rows = data.get("rows", []) if data else []
        material_query = (data or {}).get("material_query")

        if not rows:
            if material_query:
                blocks.append(
                    SectionBlock(text=f"No livewire shortage rows matched *{material_query}* in the latest snapshot.")
                )
            else:
                blocks.append(SectionBlock(text="No livewire material shortages in the latest snapshot."))
            return blocks

        snapshot = rows[0].get("snapshot_ts")
        scope = f" for *{material_query}*" if material_query else ""
        blocks.append(SectionBlock(text=f"*Livewire Demand Snapshot*{scope}"))
        blocks.append(DividerBlock())

        sorted_rows = sorted(
            [r for r in rows if float(r.get("delta_lbs") or 0.0) < 100], key=lambda r: float(r.get("delta_lbs") or 0.0)
        )

        if not sorted_rows:
            blocks.append(SectionBlock(text="_All materials are well-stocked (all deltas ≥ 100 lbs)._"))
            if snapshot:
                blocks.append(ContextBlock(elements=[MarkdownTextObject(text=f"Snapshot: {snapshot}")]))
            return blocks

        col_w = {"material": 20, "dia": 11, "delta": 10, "req": 9, "shed": 9, "head": 9, "lots": 4}
        header_row = (
            f"{'Material':<{col_w['material']}} {'Dia Range':<{col_w['dia']}} {'Delta':>{col_w['delta']}} "
            f"{'Req lbs':>{col_w['req']}} {'Shed lbs':>{col_w['shed']}} {'Head lbs':>{col_w['head']}} {'Lots':>{col_w['lots']}}"
        )
        sep = "-" * len(header_row)
        table_lines = [header_row, sep]
        for row in sorted_rows:
            material = (row.get("material_name") or row.get("material_code") or "")[: col_w["material"]]
            dia = f"{row.get('wire_dia_min')} – {row.get('wire_dia_max')}"
            delta = float(row.get("delta_lbs") or 0.0)
            delta_str = f"{'-' if delta < 0 else '+'}{abs(delta):.1f}"
            table_lines.append(
                f"{material:<{col_w['material']}} {dia:<{col_w['dia']}} {delta_str:>{col_w['delta']}} "
                f"{row.get('required_lbs') or ''!s:>{col_w['req']}} "
                f"{row.get('shed_farm_lbs') or ''!s:>{col_w['shed']}} "
                f"{row.get('head_backup_lbs') or ''!s:>{col_w['head']}} "
                f"{row.get('selected_lot_count') or ''!s:>{col_w['lots']}}"
            )
        blocks.append(SectionBlock(text="```" + "\n".join(table_lines) + "```"))

        if snapshot:
            blocks.append(ContextBlock(elements=[MarkdownTextObject(text=f"Snapshot: {snapshot}")]))

        return blocks

    @staticmethod
    def format_error(error_msg: str) -> list[Block]:
        """Format an error message"""
        return [
            SectionBlock(text=f"_Unable to process your request:_\n{error_msg}"),
        ]

    @staticmethod
    def format_api_unavailable(endpoint: str) -> list[Block]:
        labels = {
            "backlog": "backlog",
            "heading": "heading metrics",
            "heading_overall": "heading plan-vs-actual metrics",
            "livewire_demand": "livewire demand data",
            "livewire_inventory": "wire inventory data",
            "livewire_usage": "wire usage data",
        }
        label = labels.get(endpoint, "live data")
        return [
            SectionBlock(
                text=(
                    f"I understood your request, but {label} is not available from FastAPI right now.\n"
                    "I can still handle simple chat and help prompts while the data service is unavailable."
                )
            ),
        ]

    @staticmethod
    def format_unsupported_query() -> list[Block]:
        """Format message for unsupported queries"""
        catalog = "\n".join(f"• *{name}* — {desc}" for name, desc in API_CATALOG)
        return [
            SectionBlock(
                text=f"I can help you with:\n{catalog}\n\n"
                "Try asking: _What's the current backlog?_, _How many machines did we sell this week?_ "
                "or _How much mild steel should I order?_"
            ),
        ]

    @staticmethod
    def format_help() -> list[Block]:
        catalog = "\n".join(f"• *{name}* — {desc}" for name, desc in API_CATALOG)
        return [
            SectionBlock(
                text="Here's what I'm connected to:\n"
                f"{catalog}\n\n"
                "Try: _what's the current backlog?_ or _what's sales look like today?_"
            ),
        ]
