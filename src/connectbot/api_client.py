"""
FastAPI client for querying Connect FastAPI server
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class FastAPIClient:
    """Client for communicating with the Connect FastAPI server"""

    _RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_retries = max(0, max_retries)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self.client = httpx.Client(timeout=timeout_seconds)

    def _headers(self) -> dict[str, str]:
        """Get request headers with API key"""
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
        }

    def _request_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        url = f"{self.base_url}{path}"
        total_attempts = self.max_retries + 1

        for attempt in range(1, total_attempts + 1):
            try:
                response = self.client.get(url, headers=self._headers(), params=params)

                if response.status_code in self._RETRYABLE_STATUS_CODES and attempt < total_attempts:
                    logger.warning(
                        "FastAPI %s returned %s (attempt %s/%s), retrying",
                        path,
                        response.status_code,
                        attempt,
                        total_attempts,
                    )
                    time.sleep(self.retry_backoff_seconds * attempt)
                    continue

                response.raise_for_status()
                return response.json()

            except (httpx.ConnectError, httpx.TimeoutException) as error:
                if attempt >= total_attempts:
                    logger.error(
                        "FastAPI %s failed after %s attempt(s): %s",
                        path,
                        total_attempts,
                        error,
                    )
                    return None

                logger.warning(
                    "FastAPI %s transient error (attempt %s/%s): %s",
                    path,
                    attempt,
                    total_attempts,
                    error,
                )
                time.sleep(self.retry_backoff_seconds * attempt)

            except httpx.HTTPStatusError as error:
                status_code = error.response.status_code
                logger.error(
                    "FastAPI %s failed with status %s: %s",
                    path,
                    status_code,
                    error.response.text[:300],
                )
                return None

            except httpx.HTTPError as error:
                logger.error("FastAPI %s request error: %s", path, error)
                return None

        return None

    # Only endpoints on connectFastAPI's verified surface are wired here.
    # The mfgreq/trimmer/context endpoints were removed during the clean-slate
    # rebuild (2026-07-13) and come back one at a time as their compute jobs
    # are verified.

    # Optional equality filters shared by /backlog/breakdown and /backlog/lots.
    _LOT_FILTER_KEYS = (
        "stud_size",
        "stud_material",
        "stud_mat_code",
        "stud_flange",
        "stud_type",
        "req_customer",
        "req_header",
        "req_status",
        "trim_level",
        "stud_id",
    )

    def get_backlog(self) -> dict[str, Any] | None:
        """Get latest backlog snapshot"""
        return self._request_json("/v1/metrics/backlog")

    def get_backlog_breakdown(
        self,
        group_by: str,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Get open backlog grouped by a stud/request dimension (size, material, customer, ...)."""
        params: dict[str, Any] = {"group_by": group_by, "limit": limit}
        for key in self._LOT_FILTER_KEYS:
            value = (filters or {}).get(key)
            if value is not None:
                params[key] = value
        return self._request_json("/v1/metrics/backlog/breakdown", params=params)

    def get_backlog_lots(
        self,
        limit: int = 100,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Get per-lot open backlog rows with stud dimensions, optionally filtered."""
        params: dict[str, Any] = {"limit": limit}
        for key in self._LOT_FILTER_KEYS:
            value = (filters or {}).get(key)
            if value is not None:
                params[key] = value
        return self._request_json("/v1/metrics/backlog/lots", params=params)

    def get_health(self) -> dict[str, Any] | None:
        """Get FastAPI health status."""
        return self._request_json("/health")

    def get_heading(self, head_name: str | None = None, data_date: str | None = None) -> dict[str, Any] | None:
        """Get heading daily metrics, optionally filtered by head_name and/or data_date"""
        params: dict[str, Any] = {}
        if data_date:
            params["data_date"] = data_date

        path = f"/v1/metrics/heading/{head_name}" if head_name else "/v1/metrics/heading"

        return self._request_json(path, params=params)

    def get_heading_overall(self, data_date: str | None = None) -> dict[str, Any] | None:
        """Get plan-vs-actual volume/rate/utilization aggregated across all headers."""
        params: dict[str, Any] = {}
        if data_date:
            params["data_date"] = data_date
        return self._request_json("/v1/metrics/heading/overall", params=params)

    def get_heading_summary(self, data_date: str | None = None) -> dict[str, Any] | None:
        """Get WTD/MTD/YTD stud totals plus day-over-day trend."""
        params: dict[str, Any] = {}
        if data_date:
            params["data_date"] = data_date
        return self._request_json("/v1/metrics/heading/summary", params=params)

    def get_livewire_demand(
        self,
        shortages_only: bool = False,
        material_query: str | None = None,
    ) -> dict[str, Any] | None:
        """Get latest 4-week wire demand vs inventory by material and dia range.

        material_query filters client-side (case-insensitive substring on
        material code/name) — the API returns the full snapshot (~30 rows).
        The applied filter is echoed back as data["material_query"] for
        formatters.
        """
        params: dict[str, Any] = {}
        if shortages_only:
            params["shortages_only"] = "true"
        data = self._request_json("/v1/metrics/livewire/demand", params=params)
        if data is None:
            return None
        if material_query:
            needle = material_query.strip().lower()
            rows = [
                row
                for row in data.get("rows", [])
                if needle in str(row.get("material_code", "")).lower()
                or needle in str(row.get("material_name", "")).lower()
            ]
            data = {**data, "rows": rows, "count": len(rows), "material_query": material_query}
        return data

    def get_livewire_inventory(self, location: str | None = None) -> dict[str, Any] | None:
        """Get latest spool inventory levels per wire, optionally for one location (SHED/HEAD/FARM)."""
        params: dict[str, Any] = {}
        if location:
            params["location"] = location
        return self._request_json("/v1/metrics/livewire/inventory", params=params)

    def get_livewire_usage(self, dimension: str | None = None) -> dict[str, Any] | None:
        """Get wire usage over 30/90/180/365 days by vendor, material, or wire_dia."""
        params: dict[str, Any] = {}
        if dimension:
            params["dimension"] = dimension
        return self._request_json("/v1/metrics/livewire/usage", params=params)

    def get_sage_daily(self, data_date: str | None = None) -> dict[str, Any] | None:
        """Get Sage daily sales/quotes snapshot (totals, mix, top items)."""
        params: dict[str, Any] = {}
        if data_date:
            params["data_date"] = data_date
        return self._request_json("/v1/metrics/sage/daily", params=params)

    def get_sage_summary(self, data_date: str | None = None) -> dict[str, Any] | None:
        """Get Sage WTD/MTD/YTD quotes & sales rollups plus day-over-day trend."""
        params: dict[str, Any] = {}
        if data_date:
            params["data_date"] = data_date
        return self._request_json("/v1/metrics/sage/summary", params=params)

    def get_sage_trend(self, days: int = 30, end_date: str | None = None) -> dict[str, Any] | None:
        """Get per-day quotes/sales series over the trailing N weekdays."""
        params: dict[str, Any] = {"days": days}
        if end_date:
            params["end_date"] = end_date
        return self._request_json("/v1/metrics/sage/trend", params=params)

    def get_equipment_sold(
        self,
        window: str = "week",
        start_date: str | None = None,
        end_date: str | None = None,
        equip_type: str | None = None,
        group_by: str = "model",
    ) -> dict[str, Any] | None:
        """Get equipment UNITS sold by model/item/type/day (no dollar figures)."""
        params: dict[str, Any] = {"window": window, "group_by": group_by}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if equip_type:
            params["equip_type"] = equip_type
        return self._request_json("/v1/metrics/equipment/sold", params=params)

    def get_equipment_stock(self, equip_type: str | None = None, model: str | None = None) -> dict[str, Any] | None:
        """Get finished equipment ready in stock by type/model/spec."""
        params: dict[str, Any] = {}
        if equip_type:
            params["equip_type"] = equip_type
        if model:
            params["model"] = model
        return self._request_json("/v1/metrics/equipment/stock", params=params)

    def get_equipment_builds(self, status: str | None = None, stage: str | None = None) -> dict[str, Any] | None:
        """Get open equipment build requests with stage, completion % and owed items."""
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        if stage:
            params["stage"] = stage
        return self._request_json("/v1/metrics/equipment/builds", params=params)

    def get_equipment_parts(self, low_only: bool = True, limit: int = 50) -> dict[str, Any] | None:
        """Get equipment BOM parts stock vs minimum (low-stock list by default)."""
        params: dict[str, Any] = {"low_only": low_only, "limit": limit}
        return self._request_json("/v1/metrics/equipment/parts", params=params)

    def get_equipment_repairs(self, stage: str | None = None) -> dict[str, Any] | None:
        """Get open equipment repair tickets with customer, stage and dates."""
        params: dict[str, Any] = {}
        if stage:
            params["stage"] = stage
        return self._request_json("/v1/metrics/equipment/repairs", params=params)

    def get_equipment_repair_history(
        self,
        customer: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any] | None:
        """Get closed equipment repair tickets, most recently received first."""
        params: dict[str, Any] = {"limit": limit}
        if customer:
            params["customer"] = customer
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return self._request_json("/v1/metrics/equipment/repairs/history", params=params)

    def get_compute_runs(self, job_name: str | None = None, limit: int = 50) -> dict[str, Any] | None:
        """Get compute job health: latest run per job with 24h stats and history."""
        params: dict[str, Any] = {"limit": limit}
        if job_name:
            params["job_name"] = job_name
        return self._request_json("/v1/metrics/compute/runs", params=params)

    def close(self):
        """Close the HTTP client"""
        self.client.close()
