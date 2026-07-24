from __future__ import annotations

import os
from typing import Any

import httpx


class DeployerApiError(RuntimeError):
    pass


class DeployerClient:
    def __init__(self) -> None:
        api_url = os.environ.get("DEPLOYER_API_URL", "http://localhost:8000/api/v1")
        token = os.environ.get("DEPLOYER_API_TOKEN")
        if not token:
            raise RuntimeError("DEPLOYER_API_TOKEN is required")
        self.api_url = api_url.rstrip("/")
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float = 60,
    ) -> Any:
        try:
            response = httpx.request(
                method,
                f"{self.api_url}{path}",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.token}",
                },
                json=json,
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise DeployerApiError(f"Unable to reach Deployer: {exc}") from exc

        if response.is_error:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise DeployerApiError(
                f"Deployer API returned {response.status_code}: {detail}"
            )
        if response.status_code == 204:
            return None
        return response.json()
