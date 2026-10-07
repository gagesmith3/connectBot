# connectBot Workspace Guidance

## System Role
- `connectBot` is the natural-language consumer layer for the operational system.
- Its job is to turn Slack questions into grounded API-backed answers, not to become a second compute or database layer.

## Production Context
- `main.py` starts the bot.
- Core orchestration is in `src/connectbot/orchestrator.py`, handlers in `src/connectbot/handlers.py`, API access in `src/connectbot/api_client.py`, and answer formatting in `src/connectbot/response_formatter.py`.

## Connected Projects
- `connectFastAPI` is the primary data source for answers.
- `connectCompute` is the upstream source of most durable operational metrics.
- `htdocs` remains useful for validating business language and workflow semantics.
- `connectVision` and `piSensor` are edge data producers and should not be queried directly by the bot.

## Working Rules
- Prefer grounded answers from FastAPI over model-only summaries.
- When a user asks for data that does not exist in the API yet, identify the missing compute or API contract instead of improvising unsupported answers.
- Keep Slack responses concise, operational, and traceable to API evidence.