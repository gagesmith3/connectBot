# connectBot improvements and known issues

Ranked by value for the effort. When one ships, delete it here and log it in
PROGRESS.md. Evidence notes describe what was observed on 2026-10-07.

## 1. Routing regression tests (high)
`_resolve_keyword_intent` is ~200 lines of ordered regex rules, and several rules
exist only to stop an earlier rule from stealing a query ("machines sold" must hit
`equipment_sold`, not gated `sales`). Nothing tests this; `test_setup.py` only
checks env and connectivity.
- Add `tests/test_routing.py`: a table of `query → (endpoint, params)` run
  against `_resolve_keyword_intent` and `_classify_query`, with no network access.
- Seed it with real questions from `logs/connectbot.log` ("Got message from").

## 2. Capability lists that disagree with API_CATALOG (quick win)
`_RETIRED_MESSAGE`, the low-confidence clarification prompt, and the
"unsupported" reply in `orchestrator.py` hard-code a capability list that leaves out
equipment and the data pipeline. Build them from `API_CATALOG`.

## 3. Stale docs and config template (quick win)
- README / QUICK_REFERENCE still describe the Pi/systemd/ngrok setup and the
  pre-July file list; no mention of the tool router, equipment, the bridge, or
  Node-managed running.
- `.env.example` is missing `USE_TOOL_ROUTER`, `SALES_ACCESS_USERS`,
  `SLACK_NOTIFY_CHANNEL_SECONDARY`.

## 4. FastAPI endpoints the bot doesn't use yet
Exposed by connectFastAPI `routers/metrics.py` but not wired into the bot:
- `/equipment/serial/{serial}`: "what's the history on serial X?"
- `/requests/{req_lot}`: lot lookup (may let "manufacturing lots" leave `_RETIRED_TOPICS`)
- `/studs`, `/studs/{stud_id}`: stud spec lookup
- `/livewire/demand/capacity`, `/livewire/demand/tickets`
- `/matcert/lots`, `/matcert/lot/{req_lot}`, `/matcert/heat/{cert_heat}`: material certs
- `/plating/lots`
Use `skills/connectbot/SKILL.md` for each one.

## 5. Re-check retired topics
Trimmers, mfgreq lots/adjustments, data quality, ETA/lead time, and the manufacturing
summary still return "being rebuilt". Check which compute jobs have been rebuilt since
July and turn those back on.

## 6. Conversation memory is lost on every restart
History, `_business_context`, and `_last_mode` live in process dicts. The log shows
192 starts since March, so every restart wipes follow-up context. Option: persist
the last N turns per conversation (SQLite or a JSON file under `logs/`) with a TTL.

## 7. Slack connectivity noise
~7k `getaddrinfo failed` / "failed to check the current session" errors
(DNS/network outages; most recent 2026-10-04, plus connection resets on
2026-10-06). Socket Mode reconnects on its own, but nothing alerts when the bot is
offline for long. Consider exposing a "last Slack event / connected" field on
`/internal/health` for the Node ops dashboard.

## 8. Free-model fallback churn
~100 warnings from `:free` OpenRouter models failing (rate limits). Now that
`gpt-oss-120b` is paid and primary, review whether the free fallbacks still help
or just add latency before failure.

## 9. Split orchestrator.py (~1,600 lines)
Move the keyword router (`_resolve_keyword_intent` + term tables) into
`keyword_router.py` and evidence helpers into their own module. Do this after #1
so the tests protect the move.

## 10. Retire the legacy IntentParser path?
The tool router now handles anything the keyword rules miss; `IntentParser.parse` is the
last fallback (and the only router under Ollama). Decide whether to keep it for
Ollama only or remove it.

## Ideas (not yet scoped)
- Scheduled morning rundown posted to a channel (tool-router rundown + cron in the Node backend).
- Proactive alerts: wire shortage or failing compute job → post via the bridge.
- Slash command (`/connect backlog`) for quick lookups without the LLM.
