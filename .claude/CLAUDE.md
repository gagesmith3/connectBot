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
  Runtime deps live in `requirements.txt`; dev tools (pytest/ruff/mypy, pinned) in
  `requirements-dev.txt`. `pyproject.toml` holds tool config only; this is not a package.
- Log: `logs\connectbot.log` (5 MB rotating). Grep `classified query as`,
  `Tool router called`, `Trying OpenRouter model` to see routing decisions.
- The notification bridge listens on `:3010` when `CONNECTBOT_INTERNAL_API_TOKEN` is set.
- Git: local repo on `main`, no remote. `cdfff30` is the pre-modernization baseline.

## Request flow

`handlers.py` (app_mention / DM, loading animation, `[DEV]` / `[DEV FULL]`
trace prefixes) → `ConnectBotOrchestrator.process_query`:

1. **Classify** general vs business: FastAPI-connectivity check → deterministic
   social replies → `_BUSINESS_KEYWORDS` → short follow-up keeps the last mode →
   LLM classifier as the last resort.
2. **Business path**, in order:
   - rundown/multi-topic question (`_RUNDOWN_TERMS`, ≥2 `_TOPIC_GROUPS`) → straight to tool router
   - `keyword_router.resolve_keyword_intent` (deterministic regex rules; **order matters**:
     equipment before sales, retired topics before backlog/heading)
   - `_resolve_followup_intent` (reuses last endpoint, e.g. "and yesterday?")
   - tool router (`tool_router.py`: LLM picks ≤4 tools/round, ≤3 rounds)
   - legacy `IntentParser.parse` as the final fallback
3. Sales gate (`SALES_ACCESS_USERS`) applies to `sales`/`sage_trend` on every path.
4. `_call_api` → `FastAPIClient` → `/v1/metrics/*`. Evidence goes through
   `evidence.trim_evidence` (≤80 rows) to the LLM for a short Slack-mrkdwn answer.
   `livewire_demand` is formatted deterministically, with no LLM.

LLM: OpenRouter (`openrouter_client.py`, ordered model fallback, tool calling)
or Ollama (`ollama_client.py`, no tool calling, so the tool router switches itself off).

### Module map (`src/connectbot/`)

| Module | Owns |
|---|---|
| `handlers.py` | Slack events, loading animation, `[DEV]` traces, builds the clients |
| `orchestrator.py` | classify → route → call API → synthesize; prompts; sales gate; reply text |
| `keyword_router.py` | term tables, date parsing (`_extract_data_date`), `resolve_keyword_intent` |
| `tool_router.py` | `TOOL_DEFS` + `run_tool_loop` (LLM tool calling) |
| `evidence.py` | `trim_evidence`, `[DEV]` request/response previews, evidence lines |
| `api_client.py` | `FastAPIClient`, GET-only, retries |
| `response_formatter.py` | `API_CATALOG`, `capability_summary()`, Slack blocks |
| `intent_parser.py` | legacy LLM/keyword parser (last fallback; only router under Ollama) |
| `notification_bridge.py` | `:3010` internal notify + WooCommerce webhook |
| `config.py` | `BotSettings.from_env` |

## Data sources

There are two read APIs. **connectBot uses only connectFastAPI.**

- **connectFastAPI** (`:8100`, `X-API-Key`): reads connectCompute's `compute_*`
  snapshots; it has no write routes and its DB user is SELECT-only. This is the bot's only source.
- **Connect Core API** (`E:\laragon\www`, `:9090`, `/api/v1`): live `iwt_db` data,
  but it has **83 write routes**, and its session login is initials plus a 4-digit PIN.
  **Not adopted.** An LLM choosing tools must not be the thing standing between
  a question and a write. IMPROVEMENTS lists the preconditions for adopting it
  (a read-only RBAC service account, a GET-only client, a live-vs-snapshot rule,
  a real credential).

**Rule:** no connectBot client may issue a non-GET to a data API.
`tests/test_api_client_readonly.py` enforces it for `FastAPIClient`; any new
client gets the same test. (The bot *receives* POSTs on its own bridge, which is
a different direction.)

## Rules

- **Grounded or nothing.** Business answers come from FastAPI evidence. If the API
  lacks the data, the fix belongs in connectCompute/connectFastAPI (see the
  `/compute-api` skill in htdocs). Don't make the bot estimate it.
- **Endpoint names are shared keys.** A tool name in `TOOL_DEFS`, the keyword
  intent's `endpoint`, and the branch in `_call_api` must all match.
- `API_CATALOG` in `response_formatter.py` is the single list of capabilities. Help,
  "what can you do", and every "try asking about…" reply (`capability_summary()`) are
  built from it, so update it with every endpoint change. Never hard-code a capability list.
- Retired data domains (`_RETIRED_TOPICS`) get an honest "being rebuilt"
  reply instead of a doomed API call. Remove a topic once its endpoint returns.
- Never execute a tool the model wasn't offered (access control lives in the
  offered tool list).
- Slack output: `*single asterisks*`, no markdown tables/headings, sections
  ≤2900 chars (`_split_sections`).
- Never commit `.env`. It holds the Slack, OpenRouter, FastAPI, and WooCommerce secrets.

## Testing a change

```sh
venv\Scripts\python.exe -m pytest      # ~150 tests, no network, ~1s
venv\Scripts\ruff.exe check .  &&  venv\Scripts\ruff.exe format --check .
venv\Scripts\mypy.exe                  # src/connectbot + main.py
```

All four must be clean before a commit. The tests use fakes (`tests/conftest.py`):
`FakeLLM`/`FakeChatLLM` (scripted replies) and `FakeFastAPIServer` (the real
`FastAPIClient` over an `httpx.MockTransport`). Today is frozen at 2026-10-07.
- Routing change? Add rows to `tests/test_keyword_routing.py` first, including a
  nearby phrasing that must **not** match.
- Pytest is configured to never collect root `test_setup.py`. That script makes live
  connectivity checks: `venv\Scripts\python.exe test_setup.py`.

Then on the live bot: restart via the ops API, DM `[DEV] <question>`, and check
`logs\connectbot.log` for `classified query as` / `Tool router called`.
