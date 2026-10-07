---
name: connectbot
description: Use when adding, modifying, or debugging connectBot (e:\connectBot), the Slack bot that answers ops questions from connectFastAPI. Covers wiring a new FastAPI endpoint into the bot, routing/keyword rules, the tool router, sales gating, retired topics, the Slack notification bridge, and logging progress. Triggers on "connectbot", "slack bot", "bot doesn't understand", "teach the bot", "add to the bot", "tool router", "notification bridge", "woocommerce webhook".
---

# Working with connectBot

**Read `e:\connectBot\.claude\CLAUDE.md` first** (request flow plus rules). When
you finish, log the change in `.claude/PROGRESS.md` and remove anything it
resolved from `.claude/IMPROVEMENTS.md`.

If the data doesn't exist in connectFastAPI yet, stop. That work belongs
upstream (`/compute-api` skill in htdocs). The bot never estimates numbers.

## Wire a new FastAPI endpoint into the bot

Pick one snake_case **endpoint name** (e.g. `equipment_serial`) and use it
in every step below. All the steps key on that one name.

- [ ] `api_client.py`: `get_<thing>()` calling `self._request_json("/v1/metrics/...", params=...)`;
      drop `None` params.
- [ ] `orchestrator.py` `_call_api`: `if endpoint == "<name>": return self.fastapi_client.get_<thing>(...)`.
- [ ] `tool_router.py` `TOOL_DEFS`: add a tool with that name. The description should
      say when to use it **and when not to** (point to the neighbouring tool).
      Use enums for closed value sets.
- [ ] `orchestrator.py` `_resolve_keyword_intent`: add a deterministic rule only
      if the phrasing is unambiguous. Mind the order: equipment rules come before sales, and
      retired topics come before backlog/heading. Return `None` for drill-down phrasing so
      the tool router picks the parameters.
- [ ] `_BUSINESS_KEYWORDS` / `_TOPIC_GROUPS` if the new domain's nouns aren't covered.
- [ ] `_trim_evidence` if responses can be large (>80 rows, or rows need a non-default order).
- [ ] Dollar figures? Add to `_SALES_ENDPOINTS` so the per-user gate applies.
- [ ] `response_formatter.py` `API_CATALOG`: one line. Help and "what can you do" read from it.
- [ ] If it replaces a retired domain, remove that entry from `_RETIRED_TOPICS`.
- [ ] Routing tests in `tests/test_routing.py` (once they exist; IMPROVEMENTS #1).

## Verify

1. Restart: `curl -X POST http://127.0.0.1:3000/ops/api/actions/python/connectbot/restart`
   (never `python main.py` alongside the managed instance).
2. DM the bot `[DEV] <question>`. Check `classified_mode`, `endpoint`, the parameters,
   and `decision_path`. Try 3 or more phrasings, including a follow-up ("and yesterday?").
3. `logs\connectbot.log`: `Tool router called <name>(...) -> ok`, and no
   `returned no data`.
4. Ask a nearby question that should **not** hit the new endpoint, to catch rules
   stealing queries from other endpoints.

## Notification bridge (`notification_bridge.py`, :3010)

- `POST /internal/slack/notify` with header `X-ConnectBot-Token` and body
  `{webhook_key, payload:{text, blocks?, attachments?}}`. `webhook_key` maps to
  `SLACK_NOTIFY_CHANNEL_<KEY>` and falls back to `default`. A new key needs both
  `config.py` (`slack_notification_channels`) and `.env`.
- PHP callers go through htdocs `slack/slack_queue_helper.php`. For user-action
  triggers use `notify_andon_group()` (`/user-system` skill), not raw bridge calls.
- `POST /webhooks/woocommerce`: only `order.created`, HMAC-SHA256 against
  `WOOCOMMERCE_WEBHOOK_SECRET`, posts to `marketplace`.

## Debugging "the bot answered wrong"

1. `[DEV FULL] <same question>` shows the full trace, including the raw FastAPI response.
2. Wrong endpoint → keyword rule order or a tool description is the cause; fix the rule
   instead of adding prompt text.
3. Right endpoint, wrong numbers → check the API directly (`curl` with the key
   from `e:\connectFastAPI\.env`) before blaming the LLM.
4. Right data, bad wording → `_build_business_prompt` / `_build_tool_router_prompt`.
