"""Guard: the bot only ever READS from its data API.

Every public get_* method on FastAPIClient is called through a mock transport;
each must issue a GET carrying the API key. A future client for another API
(e.g. the Connect Core API) should get the same test — see .claude/CLAUDE.md
"Data sources".
"""

from __future__ import annotations

import inspect

import httpx

from src.connectbot.api_client import FastAPIClient
from tests.conftest import FakeFastAPIServer


def _getter_names() -> list[str]:
    return [name for name, _ in inspect.getmembers(FastAPIClient, inspect.isfunction) if name.startswith("get_")]


def test_every_getter_issues_get_with_api_key() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"rows": []})

    api = FastAPIClient("http://fastapi.test", "test-key", max_retries=0)
    api.client = httpx.Client(transport=httpx.MockTransport(handler))

    names = _getter_names()
    for name in names:
        method = getattr(api, name)
        required = {
            p.name: "x" for p in inspect.signature(method).parameters.values() if p.default is inspect.Parameter.empty
        }
        method(**required)

    assert len(names) >= 19, names
    assert len(requests) >= len(names)
    assert {r.method for r in requests} == {"GET"}
    assert all(r.headers["X-API-Key"] == "test-key" for r in requests)


def test_client_has_no_write_helpers() -> None:
    writes = {"post", "put", "patch", "delete", "create", "update"}
    offending = [
        name
        for name, _ in inspect.getmembers(FastAPIClient, inspect.isfunction)
        if writes & set(name.lower().strip("_").split("_"))
    ]
    assert offending == []


def test_blank_filters_are_not_sent(fake_api: FakeFastAPIServer) -> None:
    # gpt-6-luna fills every optional filter with "" — sending stud_size= filters to zero rows.
    fake_api.routes["/v1/metrics/backlog/breakdown"] = {"rows": []}
    fake_api.routes["/v1/metrics/backlog/lots"] = {"rows": []}
    client = fake_api.client()
    filters = {"stud_size": "", "stud_material": "  ", "req_customer": "ACME"}

    client.get_backlog_breakdown("stud_size", filters=filters)
    client.get_backlog_lots(filters=filters)

    for request in fake_api.requests:
        assert "stud_size" not in request.url.params
        assert "stud_material" not in request.url.params
        assert request.url.params["req_customer"] == "ACME"
