"""Minimal Apify REST client (Actor runs + dataset items).

Endpoints used (Apify API v2): POST /v2/acts/{username~name}/runs, GET /v2/actor-runs/{id},
GET /v2/datasets/{id}/items. Spending is capped per run with `maxTotalChargeUsd`.
Nothing here executes unless the caller passes a token; gating lives in ApifyActorConnector.
"""

import time
from collections.abc import Callable
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

    def run_actor(
        self,
        actor_id: str,
        run_input: dict[str, Any],
        max_total_charge_usd: float,
        on_submit: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        path = f"/v2/acts/{actor_id.replace('/', '~')}/runs"
        try:
            submit = self._http.post(
                path,
                params={"maxTotalChargeUsd": f"{max_total_charge_usd:.4f}"},
                json=run_input,
            )
            if 400 <= submit.status_code < 500:
                raise ConnectorRejected(f"Apify rejected the run: {submit.text[:240]}")
            run = self._json(submit)["data"]
            run_id = str(run["id"])
            if on_submit is not None:
                on_submit(run_id)  # recorded BEFORE any waiting, so a timeout cannot lose it
            deadline = time.monotonic() + self._max_wait
            while run["status"] not in {TERMINAL_OK, *TERMINAL_BAD}:
                if time.monotonic() > deadline:
                    raise ConnectorError(f"Apify run {run['id']} did not finish in time")
                time.sleep(self._poll)
                run = self._json(self._http.get(f"/v2/actor-runs/{run['id']}"))["data"]
        except httpx.HTTPError as exc:
            if "run_id" in locals():
                raise ProviderRunPending(
                    f"Apify transport error ({type(exc).__name__}) after submitting run {run_id}",
                    run_id,
                ) from exc
            raise ConnectorError(f"Apify transport error: {type(exc).__name__}") from exc
        if run["status"] != TERMINAL_OK:
            raise ConnectorError(f"Apify run ended with status {run['status']}")
        return run  # type: ignore[no-any-return]

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
