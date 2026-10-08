# connectBot progress log

Newest first. Add an entry whenever a change ships, with the date, what changed, and
why if it isn't obvious. Entries before 2026-10-07 were rebuilt from file
timestamps and the log, because the project had no git history. Their dates are
when the file was **last touched**, not necessarily when each feature landed.

## 2026-10-07: fix: `<tool_call>` markup posted to Slack
- Cause: when the model asked for more than 4 tools in one round, `run_tool_loop` ran 4
  and dropped the rest. Their call ids had no tool response, so OpenAI/Azure rejected the
  next request with 400 "No tool output found for function call …". The fallback
  glm-5.3-flash then wrote its tool calls as `<tool_call>…` text, which went out as the answer.
- Every call id now gets a response. The cap is 8 per round, and calls past it are
  answered "skipped, call again". `_strip_reasoning` drops `<tool_call>` markup, so a
  markup-only reply falls through to the next model.
- Checked live: 10 calls in one round → gpt-6-luna accepted the history, re-called the 2
  skipped tools, and answered.

## 2026-10-07: structured answers (answer spec + renderer + computed insights)
Design: `docs/superpowers/specs/2026-10-07-structured-answers-design.md`.
- Business answers are now a JSON **answer spec** (`answer_spec.py`) with layout
  `quick` (headline + KPI fields), `table` (monospace, phone width), `rundown` (one section
  per topic), or `prose`. The LLM names fields; `answer_renderer.py` reads the values from
  the evidence, so table and KPI numbers are never retyped by the model. Every answer gets a
  freshness footer ("backlog · as of Oct 7, 2:45 PM").
- **Computed insights** (`insights.py`): pace vs shift, projection, uptime, vs prior workday
  (heading); sales/quotes vs a 20-business-day average; backlog bottleneck/overdue and a
  breakdown's share of the total; parts below minimum; oldest open repair; wire shortages.
  The LLM may phrase up to 2 of them, cited by id; uncited ids are dropped. Warnings get ⚠.
- Applies to the single-endpoint path and the tool router. A non-JSON reply renders as
  prose, exactly as before. Kill switch: `ANSWER_SPEC_ENABLED=false`, which also skips the
  comparison fetches.
- Sales evidence now says that `*_trend_pct` for today compares a partial day with a full one.
  The bot had been reporting "sales down 88% vs yesterday" mid-afternoon.
- Fixed: the API client sent blank lot filters (`stud_size=`). gpt-6-luna fills every
  optional filter with "", so "backlog by stud size" returned no rows.
- Checked live in-process (gpt-6-luna, live FastAPI): heading, follow-up, breakdown table,
  rundown, sales, backlog, repairs. All returned valid specs with no prose fallback; wire
  demand is unchanged.
- 223 tests (was 188).

## 2026-10-07: OpenRouter model chain replaced
- `OPENROUTER_MODELS` = `openai/gpt-6-luna, z-ai/glm-5.3-flash, openai/gpt-oss-120b,
  nvidia/nemotron-3-super-120b-a12b:free` (`.env` and `.env.example`).
- Chosen by a one-off benchmark of 20 models, using the real router prompt and `TOOL_DEFS`
  (17 routing questions, 4 answer scenarios on synthetic data, 2 trials each):
  - gpt-6-luna scored 94% on routing and 100% on answers, with p90 latency of 1.9s.
  - gpt-oss-120b scored 84% on routing. It replied "no data" for "how did National 2 do
    yesterday" without calling a tool, and once leaked its `analysis…` reasoning channel.
- Removed four free models: `llama-3.3-70b-instruct:free` and `gpt-oss-20b:free` are gone
  from OpenRouter (404), and `gemma-4-31b/26b:free` were rate-limited on every call.
- `_strip_reasoning` now handles a leaked harmony `analysis` channel. It keeps the text
  after a glued final marker, or returns empty so the next model in the chain answers.
- Checked live in-process: rundown, National 2 yesterday plus a follow-up, and small talk.

## 2026-10-07: safety net + modernization
Baseline before this work: `cdfff30`.
- **Tests**: 148 pytest tests, no network, ~1s. They cover the routing table (seeded from
  real Slack questions), date parsing, classification, every orchestrator path (sales gate,
  tool router, fallbacks, retired topics, follow-ups), tool-router access control, the bridge
  (token auth, WooCommerce HMAC), config parsing, and a **GET-only guard** on the API client.
  Each suite was checked by deliberately breaking the code and watching it fail.
- **Tooling**: ruff (format + lint) and mypy, all clean; `requirements-dev.txt` (pinned);
  `pyproject.toml` (tool config only).
- **Split `orchestrator.py`** (1,640 → 937 lines) into `keyword_router.py` and `evidence.py`,
  with no behavior change (the test files were staged first and left untouched).
- **Stale replies fixed**: every "try asking about…" / clarify / retired / help reply is now
  built from `API_CATALOG` via `capability_summary()`. It previously left out equipment
  and the data pipeline. This is the one change Slack users will see.
- **Data sources decision**: FastAPI only. The Connect Core API (live data, but it has write routes) is
  not adopted; the preconditions are in IMPROVEMENTS.
- Removed Pi/systemd leftovers (`connectbot.service`, `setup.sh`) and the stale
  QUICK_REFERENCE/SLACK_SETUP docs. README rewritten; `.env.example` now includes the July settings.

## 2026-10-07: tracking set up
- Added `.claude/` (CLAUDE.md, PROGRESS.md, IMPROVEMENTS.md, connectbot skill).
- Snapshot of where things stand:
  - 18 business endpoints wired (see `TOOL_DEFS` / `API_CATALOG`); FastAPI
    exposes 10 more that the bot doesn't use yet (IMPROVEMENTS #1).
  - Primary LLM `openai/gpt-oss-120b` (paid) with `:free` model fallbacks.
  - Log since March: 85 business and 51 general classified queries; the tool router
    has handled multi-endpoint questions since July.

## 2026-07-22: runs under the Node backend
- connectBot became a managed child process of `iwt-connect/backend/server.js`
  (auto-restart), alongside connectCompute and connectFastAPI (iwt-connect b8f37dc).
- start/stop/restart actions added to htdocs `node/lib/node_ops_proxy.php`
  (htdocs b299eb3).

## ~2026-07-21: tool router and equipment data
- **Tool router** (`tool_router.py`): the LLM picks one or more FastAPI tools per
  question and writes a single answer. This enables "morning rundown"
  (heading_overall + sales + backlog) and multi-topic questions. On failure
  it falls back to legacy routing. Toggled by `USE_TOOL_ROUTER`.
- **Equipment domain**: sold units by model (week/month/YTD), finished stock,
  open builds, low-stock BOM parts, open repairs, repair history.
- **Backlog drill-down**: `backlog_breakdown` (group by size/length/material/
  customer/…) and `backlog_lots`.
- **Heading**: `heading_overall` (plan vs actual) is the default; per-head only when
  asked; `heading_summary` gives WTD/MTD/YTD.
- **Sales**: `sales` (daily + summary) and `sage_trend` (multi-day series).
- `compute_runs` for "is the data up to date?" questions.
- Date understanding: "yesterday", "N days ago", "last friday", "7/14".
- Short follow-ups reuse the previous endpoint ("and yesterday?").
- `_RETIRED_TOPICS`: honest replies for trimmers/mfgreq lots/ETA/etc. while
  their compute jobs are being rebuilt.
- `API_CATALOG` became the single source for help/capabilities text.
- Prompt tuning: conversational 1–2 sentence status answers, no reasoning
  leakage, Slack mrkdwn only.

## ~2026-07-16: OpenRouter, personality, access control
- `openrouter_client.py`: ordered model fallback, tool-calling support,
  reasoning stripped from replies, flattening for models without system prompts.
- Personality settings: `BOT_IDENTITY_NAME`, `BOT_SCOPE_NAME`, `BOT_VOICE_STYLE`,
  `BOT_PERSONALITY_NOTES`; deterministic social replies (`SOCIAL_DETERMINISTIC_MODE`).
- `SALES_ACCESS_USERS`: per-user gate on sales/quote dollar figures.
- Per-channel notify routing (`SLACK_NOTIFY_CHANNEL_*`, now including `secondary`).
- Handlers: animated loading message, `[DEV]` / `[DEV FULL]` trace mode.

## ~2026-04-06: local LLM option and agent mode
- `LLM_PROVIDER=ollama` (`ollama_client.py`, `LOCAL_ONLY_FAIL_CLOSED`) alongside OpenRouter.
- AI-first orchestrator (`USE_AGENT_MODE`): general chat vs grounded business answers,
  with rolling per-thread history.
- FastAPI client retries/backoff/timeouts; README + QUICK_REFERENCE written.
- 2026-04-16: `.github/agents/iwt-stack-bridge.agent.md` (cross-layer agent).

## ~2026-03-18: notification bridge
- `notification_bridge.py`: `POST /internal/slack/notify` (token-auth, webhook_key →
  channel) and the WooCommerce `order.created` webhook (HMAC-verified) → #marketplace.
- 2026-03-19: htdocs `slack/slack_queue_helper.php` began sending through the bridge
  (`X-ConnectBot-Token`, posting with the bot token) when it's configured (htdocs 26ee3e7).
- `.github/` copilot instructions plus the system-architect and cross-project-tracer agents.

## 2026-03-16: initial build
- Slack Bolt in Socket Mode, FastAPI client, LLM intent parser, Slack block
  formatter, handlers for mentions and DMs. Originally targeted a Raspberry Pi with systemd.
