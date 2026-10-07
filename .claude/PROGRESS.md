# connectBot progress log

Newest first. Add an entry whenever a change ships, with the date, what changed, and
why if it isn't obvious. Entries before 2026-10-07 were rebuilt from file
timestamps and the log, because the project had no git history. Their dates are
when the file was **last touched**, not necessarily when each feature landed.

## 2026-10-07: tracking set up
- Added `.claude/` (CLAUDE.md, PROGRESS.md, IMPROVEMENTS.md, connectbot skill).
- Snapshot of where things stand:
  - 18 business endpoints wired (see `TOOL_DEFS` / `API_CATALOG`); FastAPI
    exposes 10 more that the bot doesn't use yet (IMPROVEMENTS #4).
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
