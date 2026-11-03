"""Minimal Apify REST client (Actor runs + dataset items).

Endpoints used (Apify API v2): POST /v2/acts/{username~name}/runs, GET /v2/actor-runs/{id},
GET /v2/datasets/{id}/items. Spending is capped per run with `maxTotalChargeUsd`.
Nothing here executes unless the caller passes a token; gating lives in ApifyActorConnector.
"""

import time
from typing import Any

import httpx
from pydantic import SecretStr

from app.connectors.base import ConnectorError, ConnectorRejected, ProviderRunPending

TERMINAL_OK = "SUCCEEDED"
TERMINAL_BAD = {"FAILED", "ABORTED", "TIMED-OUT"}


class ApifyClient:
    def __init__(
        self,
        token: SecretStr,
        base_url: str = "https://api.apify.com",
        transport: httpx.BaseTransport | None = None,
        poll_seconds: float = 2.0,
        max_wait_seconds: float = 600.0,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token.get_secret_value()}"},
            timeout=httpx.Timeout(60.0),
            transport=transport,
        )
        self._poll = poll_seconds
        self._max_wait = max_wait_seconds

    def _json(self, resp: httpx.Response) -> Any:
        if resp.status_code >= 400:
            # never echo headers/tokens; status + short body only
            raise ConnectorError(f"Apify API {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def fetch_run(self, run_id: str) -> dict[str, Any]:
        """Read an existing run (free). Used to reconcile a run whose outcome was unknown."""
        try:
            data: dict[str, Any] = self._json(self._http.get(f"/v2/actor-runs/{run_id}"))["data"]
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Apify transport error: {type(exc).__name__}") from exc
        return data

    def monthly_usage(self) -> dict[str, Any]:
        """Provider-side usage for the current billing cycle (free, read-only)."""
        try:
            data = self._json(self._http.get("/v2/users/me/usage/monthly"))["data"]
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Apify transport error: {type(exc).__name__}") from exc
        keep = ("totalUsageCreditsUsdBeforeVolumeDiscount", "totalUsageCreditsUsdAfterVolumeDiscount")
        out = {k: data[k] for k in keep if k in data}
        out["usageCycle"] = data.get("usageCycle")
        return out

    def run_actor(
        self,
        actor_id: str,
        run_input: dict[str, Any],
        max_total_charge_usd: float,
        ledger: Any = None,
    ) -> dict[str, Any]:
        """Submit (or ATTACH to) one Actor run. With a ledger the run is recorded before the
        POST, so an uncertain submission can never be repeated."""
        path = f"/v2/acts/{actor_id.replace('/', '~')}/runs"
        row_id = None
        existing: str | None = None
        if ledger is not None:
            row_id, existing = ledger.begin(actor_id, run_input, max_total_charge_usd)
        run: dict[str, Any] = {}
        try:
            if existing is not None:
                run = self._json(self._http.get(f"/v2/actor-runs/{existing}"))["data"]
            else:
                try:
                    submit = self._http.post(
                        path,
                        params={"maxTotalChargeUsd": f"{max_total_charge_usd:.4f}"},
                        json=run_input,
                    )
                except httpx.HTTPError as exc:
                    # the request may have reached Apify: outcome unknown, never re-submit
                    if ledger is not None and row_id is not None:
                        ledger.uncertain(row_id, f"submit transport error {type(exc).__name__}")
                    raise ProviderRunPending(
                        f"Apify submit outcome unknown ({type(exc).__name__}); not re-submitting", ""
                    ) from exc
                if 400 <= submit.status_code < 500:
                    if ledger is not None and row_id is not None:
                        ledger.rejected(row_id, submit.text)
                    raise ConnectorRejected(f"Apify rejected the run: {submit.text[:240]}")
                if submit.status_code >= 500:
                    if ledger is not None and row_id is not None:
                        ledger.uncertain(row_id, f"submit HTTP {submit.status_code}")
                    raise ProviderRunPending(
                        f"Apify submit returned {submit.status_code}; outcome unknown", ""
                    )
                run = self._json(submit)["data"]
                if ledger is not None and row_id is not None:
                    ledger.submitted(row_id, run)  # BEFORE any waiting, so a timeout cannot lose it
            deadline = time.monotonic() + self._max_wait
            while run["status"] not in {TERMINAL_OK, *TERMINAL_BAD}:
                if time.monotonic() > deadline:
                    raise ConnectorError(f"Apify run {run['id']} did not finish in time")
                time.sleep(self._poll)
                run = self._json(self._http.get(f"/v2/actor-runs/{run['id']}"))["data"]
        except httpx.HTTPError as exc:
            if run.get("id"):
                raise ProviderRunPending(
                    f"Apify transport error ({type(exc).__name__}) after submitting run {run['id']}",
                    str(run["id"]),
                ) from exc
            raise ConnectorError(f"Apify transport error: {type(exc).__name__}") from exc
        if ledger is not None and row_id is not None:
            ledger.finished(row_id, run)
        if run["status"] != TERMINAL_OK:
            raise ConnectorError(f"Apify run ended with status {run['status']}")
        return run

    def dataset_items(self, dataset_id: str, limit: int) -> list[dict[str, Any]]:
        try:
            items = self._json(
                self._http.get(
                    f"/v2/datasets/{dataset_id}/items",
                    params={"format": "json", "clean": "true", "limit": limit},
                )
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Apify transport error: {type(exc).__name__}") from exc
        if not isinstance(items, list):
            raise ConnectorError("Unexpected dataset response shape")
        return items
