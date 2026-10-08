# connectBot: structured answers (answer spec + renderer + computed insights)

## Context

Every business answer today is free prose from the LLM. Its shape is controlled only by
prompt rules in `orchestrator.py:148-180`: "1-2 sentences, no tables, bullets only if asked".
`ResponseFormatter.format_grounded_answer` then splits that prose into plain section blocks.
Prose works for "how's heading today?" but is poor for rankings, comparisons and multi-topic
rundowns. The only structured answer is `livewire_demand`'s hand-built table.

Goal: the bot picks the right **shape** for each answer (quick KPI, table, sectioned
rundown, or prose) and adds **grounded insights** (vs plan, vs yesterday, share of total).
It should read like a business analyst's reply instead of a paragraph.

Decisions made with the user:
- **Answer spec.** The LLM returns JSON that chooses the layout and writes the headline and
  prose. For tables and KPIs it names *fields* only. A deterministic renderer pulls the values
  straight from the FastAPI evidence, so the LLM never retypes table or KPI numbers.
- **Insights.** Code computes them and the LLM phrases them. Code computes candidate facts,
  including **prior-period fetches** for heading and sales. The LLM picks 0-2 of them by id.
  The renderer drops any insight whose id wasn't computed.
- In scope: quick/KPI, tables, sectioned rundowns, insights.
- Deferred: charts, follow-up buttons/suggestions, and clarifying-choice UI. They go to
  IMPROVEMENTS as ideas.

## Design

### 1. `src/connectbot/answer_spec.py` (new): the contract
- `AnswerSpec` dataclass:
  - `layout`: one of `quick | table | rundown | prose`
  - `headline`: one line
  - `body`: ≤3 sentences, optional
  - `kpis`: `[{source, field, label, format}]`, ≤6
  - `table`: `{source, columns:[{field,label,format}], sort, limit}`, optional
  - `sections`: `[{title, headline, kpis, table, body}]`, rundown only, ≤5
  - `insights`: `[{id, text}]`, ≤2
- `format` ∈ `int | qty | pct | usd | date | text`.
- `parse_answer_spec(raw, evidence, fact_ids) -> AnswerSpec | None`:
  - Tolerant parsing: strip ``` fences, take the first `{…}`, and reuse
    `openrouter_client._strip_reasoning` first.
  - Validation:
    - Unknown `source`/`field` refs are dropped.
    - `limit` is clamped to 15.
    - Insights whose `id ∉ fact_ids` are dropped.
    - An unknown layout falls back to `prose`.
  - Returns `None` when there is no usable JSON. The caller then renders the raw text as prose,
    exactly as today, so the bot can't do worse than the current behavior.
- `JSON_SCHEMA_PROMPT`: schema text plus layout guidance, embedded in the prompts:
  - status question → `quick`
  - list, ranking or comparison → `table`
  - several topics → `rundown`
  - explanation → `prose`

### 2. `src/connectbot/answer_renderer.py` (new): spec + evidence → Slack blocks
- `render(spec, evidence: dict[source, payload]) -> (fallback_text, blocks)`.
- **quick**: bold headline, then a `SectionBlock(fields=…)` KPI grid (2 columns, ≤10 fields),
  optional body, insight lines (`• …`), and a footer.
- **table**: headline, then a monospace code-block table built from `payload["rows"]`.
  - Columns sized for phone width (~44 chars): long labels truncated with `…`, numbers right-aligned.
  - An "…and N more" line, then insights and the footer.
  - Reuse/extract the column logic from `ResponseFormatter.format_livewire_demand` (`response_formatter.py:221-274`).
- **rundown**: per section a bold title, headline, KPI fields/table, and a `DividerBlock` between
  sections. It stays under Slack's 50-block and 3000-char-per-section limits, reusing `_split_sections`.
- **prose**: current `format_grounded_answer` behaviour.
- **Footer** (`ContextBlock`): source name plus freshness taken from `snapshot_ts`, `updated_at` or
  `data_date` ("backlog breakdown · snapshot 2:30 PM"). It replaces `_Grounded with FastAPI endpoint: …_`.
- Number formatting helpers (`1,420,000`, `1.42M` for compact KPIs, `73%`, `$6,501`) live here.
  `ResponseFormatter.to_text` keeps its own formatting.
- `fallback_text`: headline + body + insight texts, as plain text. It is used for notifications
  and stored in `_history`, so typed follow-ups keep working.

### 3. `src/connectbot/insights.py` (new): computed facts
- `Fact(id, text, severity)`. Registry `{endpoint: fn(data, comparison) -> list[Fact]}`.
  Endpoints without a function return `[]`.
- Initial set:
  - `heading_overall`:
    - volume vs plan
    - projected end-of-shift vs plan (`projected_volume_pct`)
    - uptime vs plan
    - **projected vs prior workday's final `volume_pct`**, like for like and never a partial day
      against a full day
  - `heading` (per head): actual vs plan; worst/best heads when there are many rows.
  - `sage_daily`: sales and quotes so far vs the **trailing 20-weekday average** from `sage_trend`,
    labelled "so far today".
  - `backlog_breakdown`: top group's share of the total; top 3 share.
  - `backlog`: heading/trim/plate split.
  - `equipment_parts`: count below minimum.
  - `equipment_repairs`: oldest open ticket age.
  - `livewire_demand`: shortages count. It stays deterministic; facts are used only in a rundown.
- `COMPARISON_FETCHES`: `{heading_overall: prior workday via data_date, sage_daily: sage_trend(days=20)}`.
  - Prior workday skips weekends.
  - Reuse the date helpers in `keyword_router.py` (`_extract_data_date` area) if one fits.
  - The fetch goes through the orchestrator's `_call_api`, so it stays GET-only, uses the same
    client, and respects the sales gate: no sage comparison for a restricted user.
- If a comparison fetch fails or returns empty, the comparison facts are skipped and the answer still proceeds.

### 4. Orchestrator integration (`orchestrator.py`)
- New helper `_gather(endpoint, params) -> (payload, facts)`. It calls `_call_api`, then the
  comparison fetch, then `compute_insights`.
- **Single-endpoint path** (`_handle_business_query`, lines 687-752):
  - The user message carries the trimmed evidence plus `Computed facts: [{id, text}]`.
  - `business_prompt` output rules are rewritten to require the JSON spec.
  - The reply goes through `parse_answer_spec`, then `render`, falling back to prose.
  - The LLM-failure fallback (`to_text`) is unchanged.
- **Tool-router path** (`_handle_tool_router_query`, `tool_router.run_tool_loop`):
  - The `call_api` callback passed to the loop becomes `_gather`-backed.
  - Each tool result message is `{"data": trimmed, "computed_facts": [...]}`.
  - `run_tool_loop` additionally returns `evidence: dict[tool_name, payload]` and `fact_ids`,
    keyed by tool name. When the same tool is called twice with different args, the key is
    suffixed (`heading#2`).
  - The final answer is parsed and rendered the same way, so a morning rundown becomes the `rundown` layout.
- `livewire_demand`, retired, restricted, empty-data, unavailable and general/social paths: **unchanged**.
- `[DEV]`: add `answer_layout`, `answer_spec` (parsed), `facts` and `spec_fallback_reason` to `dev_info`.
- Kill switch: `ANSWER_SPEC_ENABLED` (default `true`) in `config.py` `BotSettings` and in
  `.env.example`. When off, the current prompt and prose path run unchanged.

### 5. Docs
- connectBot `.claude/CLAUDE.md`:
  - The Slack-output rule now says that structured answers come from `answer_spec` plus the
    renderer, and the LLM never formats tables itself.
  - Add `answer_spec`, `answer_renderer` and `insights` to the module map.
- `skills/connectbot/SKILL.md` checklist: "add an `insights.py` function if the endpoint has
  a natural comparison".
- `PROGRESS.md` entry.
- `IMPROVEMENTS.md`: add the deferred items:
  - charts (local matplotlib + `files:write`)
  - follow-up buttons (needs interactivity + `app.action`)
  - clarifying-choice UI
  - Slack's native `table` block as a later swap for the code-block table

## Build order (TDD; each phase is shippable)
1. `answer_spec` + `answer_renderer` + single-endpoint path (quick/table/prose) + kill switch.
2. Tool-router evidence plumbing + `rundown` layout.
3. `insights` + comparison fetches, wired into both paths.

Tests are written first in each phase:
- `tests/test_answer_spec.py`: fenced or partial JSON, bad refs dropped, unknown insight id dropped,
  limit clamp, `None` on garbage.
- `tests/test_answer_renderer.py`:
  - Table values come from the evidence, not the spec (a spec carrying a fake number must not
    show it).
  - Width ≤ 44, "…and N more", block and char limits, footer freshness, and a rundown with
    2 sections.
- `tests/test_insights.py`: each fact function; prior-workday skips weekends (today is frozen
  at 2026-10-07, a Wednesday); comparison fetch failure is tolerated; a restricted user gets
  no sage comparison.
- `tests/test_orchestrator_flow.py` and the tool-router tests:
  - `FakeLLM` returning a JSON spec → table blocks.
  - Returning prose → prose fallback.
  - Kill switch off → today's output.
  - Tool loop returns evidence keyed by tool.

Execution follows superpowers TDD per phase. After approval, the plan is saved as the spec at
`e:\connectBot\docs\superpowers\specs\2026-10-07-structured-answers-design.md`.

## Verification
1. `venv\Scripts\python.exe -m pytest`, `ruff check .`, `ruff format --check .` and `mypy` are all clean.
2. Restart: `curl -X POST http://127.0.0.1:3000/ops/api/actions/python/connectbot/restart`.
3. DM `[DEV]` and check `answer_layout`, the facts and any fallback reason. Then view each reply
   on desktop **and phone**:
   - "how's heading today" → `quick` with a vs-plan / vs-yesterday insight
   - "backlog by stud size" → `table` whose values match `curl …/backlog/breakdown?group_by=stud_size`
   - "morning rundown" → `rundown` with 3 sections
   - "how are sales today" → `sage` comparison insight (and none for a restricted user)
   - "what's the wire demand" → unchanged livewire table
   - "and yesterday?" follow-up still routes correctly
4. JSON compliance across the OpenRouter chain: grep `logs\connectbot.log` for
   `spec_fallback_reason` after ~10 questions. If the primary model (gpt-6-luna) falls back
   often, tighten the prompt before shipping phase 2.
