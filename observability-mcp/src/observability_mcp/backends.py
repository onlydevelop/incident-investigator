"""HTTP calls to Prometheus, Loki and Tempo. Each failure becomes a BackendError whose message says
what went wrong in terms the caller can act on: the backend's own error (a PromQL or LogQL parse
error, say), or that the backend couldn't be reached."""
from dataclasses import dataclass
from typing import Any, Optional

import httpx


class BackendError(Exception):
    pass


class NotFound(BackendError):
    pass


@dataclass
class Backend:
    name: str
    url: str
    timeout: float = 30.0
    # Tests pass an httpx.MockTransport.
    transport: Optional[httpx.AsyncBaseTransport] = None

    async def get(self, path: str, params: Optional[dict[str, Any]] = None,
                  headers: Optional[dict[str, str]] = None) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            async with httpx.AsyncClient(base_url=self.url, timeout=self.timeout, transport=self.transport) as client:
                response = await client.get(path, params=params, headers=headers)
        except httpx.TimeoutException:
            raise BackendError(f"{self.name} at {self.url} didn't answer within {self.timeout:g}s. "
                               "Narrow the time range or the query.") from None
        except httpx.HTTPError as e:
            raise BackendError(f"{self.name} is unreachable at {self.url} ({e!r}). "
                               "Is the observability stack running (make obs-up)?") from None
        if response.status_code == 404:
            raise NotFound(f"{self.name}: not found ({path})")
        if response.is_error:
            raise BackendError(f"{self.name} rejected the request (HTTP {response.status_code}): {_error_text(response)}")
        try:
            body = response.json()
        except ValueError:
            raise BackendError(f"{self.name} returned something that isn't JSON: {response.text[:300]}") from None
        # Prometheus and Loki wrap results in {"status": "success" | "error", ...}.
        if isinstance(body, dict) and body.get("status") == "error":
            raise BackendError(f"{self.name}: {body.get('error') or body}")
        return body


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text.strip()[:1000]
    if isinstance(body, dict):
        return str(body.get("error") or body.get("message") or body)[:1000]
    return str(body)[:1000]
