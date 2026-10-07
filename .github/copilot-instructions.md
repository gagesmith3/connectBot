# connectBot Workspace Guidance

## System Role
- `connectBot` is the natural-language consumer layer for the operational system.
- Its job is to turn Slack questions into grounded API-backed answers, not to become a second compute or database layer.

## Production Context
- `main.py` starts the bot (launched and supervised by the Node backend in `iwt-connect`).
- Full module map, request flow, and rules: `.claude/CLAUDE.md`. In short: orchestration in `src/connectbot/orchestrator.py`, keyword routing in `keyword_router.py`, LLM tool calling in `tool_router.py`, evidence trimming in `evidence.py`, Slack handlers in `handlers.py`, API access in `api_client.py`, formatting and `API_CATALOG` in `response_formatter.py`.
- Tests: `venv\Scripts\python.exe -m pytest` (no network); lint/types: `ruff check .`, `ruff format --check .`, `mypy`.

## Connected Projects
- `connectFastAPI` is the only data source for answers. The Connect Core API (`:9090`) is deliberately not used: it has write routes (see `.claude/CLAUDE.md` "Data sources"). The bot issues GET requests only.
- `connectCompute` is the upstream source of most durable operational metrics.
- `htdocs` remains useful for validating business language and workflow semantics.
- `connectVision` and `piSensor` are edge data producers and should not be queried directly by the bot.

## Working Rules
- Prefer grounded answers from FastAPI over model-only summaries.
- When a user asks for data that does not exist in the API yet, identify the missing compute or API contract instead of improvising unsupported answers.
- Keep Slack responses concise, operational, and traceable to API evidence.