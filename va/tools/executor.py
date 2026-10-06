"""Outbound tool HTTP executor: hard timeout, HMAC signing, structured errors."""

import hashlib
import hmac
import json
import time
from dataclasses import dataclass

import httpx
from loguru import logger

from va.config.tenants import TenantToolConfig


@dataclass
class ToolResult:
    ok: bool
    data: dict
    elapsed_ms: int
    error: str | None = None


def sign_payload(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def execute_tool(
    tool: TenantToolConfig,
    arguments: dict,
    *,
    call_id: str,
    tool_call_id: str,
    hmac_secret: str = "",
    timeout_secs: float = 10.0,
    client: httpx.AsyncClient | None = None,
) -> ToolResult:
    """POST the tool call to the tenant endpoint.

    - URL always comes from tenant config, never from LLM output.
    - Request body is HMAC-signed so the tenant can verify authenticity.
    - Write tools carry an idempotency key (call_id + tool_call_id) so retries
      after network errors cannot double-apply side effects.
    """
    payload = {
        "tool": tool.name,
        "arguments": arguments,
        "call_id": call_id,
        "idempotency_key": f"{call_id}:{tool_call_id}",
        "read_only": tool.read_only,
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json", "X-VA-Call-Id": call_id}
    if hmac_secret:
        headers["X-VA-Signature"] = sign_payload(body, hmac_secret)

    started = time.monotonic()
    try:
        timeout = httpx.Timeout(timeout_secs)
        if client is None:
            async with httpx.AsyncClient(timeout=timeout) as own_client:
                resp = await own_client.post(tool.url, content=body, headers=headers)
        else:
            resp = await client.post(tool.url, content=body, headers=headers)
        elapsed = int((time.monotonic() - started) * 1000)
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        return ToolResult(ok=True, data=data if isinstance(data, dict) else {"result": data},
                          elapsed_ms=elapsed)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a structured result
        elapsed = int((time.monotonic() - started) * 1000)
        logger.warning("Tool {} failed after {}ms: {}", tool.name, elapsed, exc)
        return ToolResult(ok=False, data={}, elapsed_ms=elapsed, error=str(exc))
