# connectBot

Slack bot (Slack Bolt, Socket Mode) that answers IWT operations questions
using live data from connectFastAPI, plus a small HTTP bridge that PHP and
WooCommerce use to post notifications into Slack. It is the natural-language
consumer at the end of the pipeline: connectCompute → connectFastAPI → connectBot.
It never touches a database directly.

Tracking files in this folder:
- `PROGRESS.md`: what has shipped, newest first. Add an entry whenever a change lands.
- `IMPROVEMENTS.md`: known issues and ideas, ranked. Move items to PROGRESS when they're done.
- `skills/connectbot/SKILL.md`: checklist for wiring a new FastAPI endpoint into the bot.

## Running it

- Runs as a **managed child of the Node backend** (`e:\iwt-connect\backend\server.js`,
  `pythonServices.connectbot`, auto-restart on). Restart with
  `curl -X POST http://127.0.0.1:3000/ops/api/actions/python/connectbot/restart`
  (`start`/`stop` work too; also exposed in htdocs `node/lib/node_ops_proxy.php`).
  **Never run `python main.py` by hand while the managed instance runs.** Two
  Socket Mode clients split Slack events between them.
- Python 3.13 venv at `venv\`. Config is `.env` only (`config.py → BotSettings.from_env`).
- Log: `logs\connectbot.log` (5 MB rotating). Grep `classified query as`,
  `Tool router called`, `Trying OpenRouter model` to see routing decisions.
- The notification bridge listens on `:3010` when `CONNECTBOT_INTERNAL_API_TOKEN` is set.
- Source of truth for the README/QUICK_REFERENCE is the code. Both docs still describe the
  April Raspberry Pi/systemd setup and are stale.

## Request flow

`handlers.py` (app_mention / DM, loading animation, `[DEV]` / `[DEV FULL]`
trace prefixes) → `ConnectBotOrchestrator.process_query`:

1. **Classify** general vs business: FastAPI-connectivity check → deterministic
   social replies → `_BUSINESS_KEYWORDS` → short follow-up keeps the last mode →
   LLM classifier as the last resort.
2. **Business path**, in order:
   - rundown/multi-topic question (`_RUNDOWN_TERMS`, ≥2 `_TOPIC_GROUPS`) → straight to tool router
   - `_resolve_keyword_intent` (deterministic regex rules; **order matters**:
     equipment before sales, retired topics before backlog/heading)
   - `_resolve_followup_intent` (reuses last endpoint, e.g. "and yesterday?")
   - tool router (`tool_router.py`: LLM picks ≤4 tools/round, ≤3 rounds)
   - legacy `IntentParser.parse` as the final fallback
3. Sales gate (`SALES_ACCESS_USERS`) applies to `sales`/`sage_trend` on every path.
4. `_call_api` → `FastAPIClient` → `/v1/metrics/*`. Evidence goes through
   `_trim_evidence` (≤80 rows) to the LLM for a short Slack-mrkdwn answer.
   `livewire_demand` is formatted deterministically, with no LLM.

LLM: OpenRouter (`openrouter_client.py`, ordered model fallback, tool calling)
or Ollama (`ollama_client.py`, no tool calling, so the tool router switches itself off).

## Rules

- **Grounded or nothing.** Business answers come from FastAPI evidence. If the API
  lacks the data, the fix belongs in connectCompute/connectFastAPI (see the
  `/compute-api` skill in htdocs). Don't make the bot estimate it.
- **Endpoint names are shared keys.** A tool name in `TOOL_DEFS`, the keyword
  intent's `endpoint`, and the branch in `_call_api` must all match.
- `API_CATALOG` in `response_formatter.py` is the single list of capabilities. Help
  text and "what can you do" are built from it, so update it with every endpoint change.
- Retired data domains (`_RETIRED_TOPICS`) get an honest "being rebuilt"
  reply instead of a doomed API call. Remove a topic once its endpoint returns.
- Never execute a tool the model wasn't offered (access control lives in the
  offered tool list).
- Slack output: `*single asterisks*`, no markdown tables/headings, sections
  ≤2900 chars (`_split_sections`).
- Never commit `.env`. It holds the Slack, OpenRouter, FastAPI, and WooCommerce secrets.

## Testing a change

No unit tests yet (see IMPROVEMENTS #1). Today:
1. `venv\Scripts\python.exe test_setup.py`: env vars and connectivity.
2. Restart via the ops API, then DM the bot with `[DEV] <question>` to see the
   classified mode, endpoint, parameters, and decision path.
3. Check `logs\connectbot.log` for the routing lines above.
