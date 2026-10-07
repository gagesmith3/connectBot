# connectBot

Slack bot that answers IWT ops questions (backlog, heading, sales, equipment,
wire) from connectFastAPI, plus an HTTP bridge (`:3010`) that PHP and
WooCommerce use to post into Slack. Read-only consumer: it never writes to a
data API.

**Start here:** [.claude/CLAUDE.md](.claude/CLAUDE.md) (architecture, rules,
data sources) · [PROGRESS](.claude/PROGRESS.md) · [IMPROVEMENTS](.claude/IMPROVEMENTS.md)

## Run

Managed by the Node backend (`e:\iwt-connect\backend`), which runs
`venv\Scripts\python.exe main.py` and restarts it if it dies. Never start a
second copy by hand.

```sh
curl -X POST http://127.0.0.1:3000/ops/api/actions/python/connectbot/restart   # or start / stop
```

Config: `.env` (copy `.env.example`). Log: `logs\connectbot.log`.
Connectivity check: `venv\Scripts\python.exe test_setup.py`.

## Develop

```sh
venv\Scripts\python.exe -m pip install -r requirements-dev.txt
venv\Scripts\python.exe -m pytest        # no network; ~1s
venv\Scripts\ruff.exe check .
venv\Scripts\ruff.exe format --check .
venv\Scripts\mypy.exe
```

All four must be clean before a commit. In Slack, prefix a DM with `[DEV]` (or
`[DEV FULL]`) to see the routing decision and raw FastAPI response.

## Slack app

Socket Mode on, with an app-level token (`xapp-…`, scope `connections:write`).
Bot token scopes: `app_mentions:read`, `chat:write`, `im:history`, `im:read`,
`im:write`. Event subscriptions: `app_mention`, `message.im`.
