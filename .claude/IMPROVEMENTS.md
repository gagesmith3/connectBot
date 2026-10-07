# connectBot improvements and known issues

Ranked by value for the effort. When one ships, delete it here and log it in
PROGRESS.md. Evidence notes describe what was observed on 2026-10-07.

## 1. FastAPI endpoints the bot doesn't use yet
Exposed by connectFastAPI `routers/metrics.py` but not wired into the bot:
- `/equipment/serial/{serial}`: "what's the history on serial X?"
- `/requests/{req_lot}`: lot lookup (may let "manufacturing lots" leave `_RETIRED_TOPICS`)
- `/studs`, `/studs/{stud_id}`: stud spec lookup
- `/livewire/demand/capacity`, `/livewire/demand/tickets`
- `/matcert/lots`, `/matcert/lot/{req_lot}`, `/matcert/heat/{cert_heat}`: material certs
- `/plating/lots`
Use `skills/connectbot/SKILL.md` for each one (tests first).

## 2. Re-check retired topics
Trimmers, mfgreq lots/adjustments, data quality, ETA/lead time, and the manufacturing
summary still return "being rebuilt". Check which compute jobs have been rebuilt since
July and turn those back on.

## 3. Routing gaps seen in real questions
`tests/test_keyword_routing.py` pins these as `None` (they go to the tool router).
Fine while the router is up; they misroute if it's down or under Ollama:
- "hows the carlo salvi doing today?": a head name with no "heading"/"header" word
- "how many machines have we made this year": no rule for machines *built*
- "what has our 1010-MS usage looked like lately?": the usage rule needs "wire"/"material"
- "how much 0.212 stainless steel do we have?": no inventory word
Add rules (and flip the test rows) only where the phrasing is unambiguous.

## 4. Legacy IntentParser: substring small-talk bug, and retire-or-keep
`IntentParser._local_first_parse` treats any query containing a small-talk keyword
as a *substring* as small talk, so "hi" matches "mac**hi**nes" and "t**hi**s".
"how many machines have we made this year" → `help`. It is reached only when the
tool router fails or under Ollama. Either fix it with word-boundary matching (like
`keyword_router._has_term`), or decide the parser is Ollama-only and retire it for OpenRouter.

## 5. Conversation memory is lost on every restart
History, `_business_context`, and `_last_mode` live in process dicts. The log shows
192 starts since March, so every restart wipes follow-up context. Option: persist
the last N turns per conversation (SQLite or a JSON file under `logs/`) with a TTL.

## 6. Slack connectivity noise
~7k `getaddrinfo failed` / "failed to check the current session" errors
(DNS/network outages; most recent 2026-10-04, plus connection resets on
2026-10-06). Socket Mode reconnects on its own, but nothing alerts when the bot is
offline for long. Consider exposing a "last Slack event / connected" field on
`/internal/health` for the Node ops dashboard.

## 7. Free-model fallback churn
~100 warnings from `:free` OpenRouter models failing (rate limits). Now that
`gpt-oss-120b` is paid and primary, review whether the free fallbacks still help
or just add latency before failure.

## 8. Connect Core API as a second read source (decision recorded, not built)
The Core API (`E:\laragon\www`, `:9090`) has live data, but also 83 write routes
behind a PIN-grade session. See CLAUDE.md "Data sources". Adopt it only once **all** of these hold:
1. a dedicated bot service account whose RBAC role holds **read permissions only**,
   so the server refuses a write, rather than the bot merely not attempting one;
2. a separate `CoreApiClient` exposing GET methods only. It never fetches or sends a CSRF
   token, and it gets the same GET-only guard test as `FastAPIClient`;
3. a per-tool rule for live vs snapshot (e.g. "where is spool X right now" → live;
   trends/totals → FastAPI), so one answer never mixes two different points in time;
4. a stronger credential than the default PIN for that account (or an API-key route).

## Ideas (not yet scoped)
- Scheduled morning rundown posted to a channel (tool-router rundown + cron in the Node backend).
- Proactive alerts: wire shortage or failing compute job → post via the bridge.
- Slash command (`/connect backlog`) for quick lookups without the LLM.
- CI (GitHub Actions running the four checks) once the repo has a remote.
