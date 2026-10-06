"""Tool engine unit tests: caller_id binding, filler timing, HMAC, error paths."""

import asyncio
import hashlib
import hmac
import time

import httpx
import pytest
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.services.llm_service import FunctionCallParams

from va.config.settings import Settings
from va.config.tenants import TenantConfig, TenantToolConfig
from va.session import CallSession
from va.tools.engine import register_tools
from va.tools.executor import execute_tool, sign_payload


class StubWorker:
    def __init__(self):
        self.frames = []

    async def queue_frame(self, frame):
        self.frames.append(frame)


class StubParams(FunctionCallParams):
    pass


def make_session(caller: str = "+919876543210") -> CallSession:
    return CallSession(
        call_id="call-test-1",
        carrier="exotel",
        stream_sid="stream-1",
        call_sid="call-test-1",
        tenant=TenantConfig(tenant_id="t", system_prompt="s"),
        caller_id=caller,
    )


def make_params(arguments: dict, worker: StubWorker):
    results = []

    async def result_callback(result, **kwargs):
        results.append(result)

    params = FunctionCallParams(
        function_name="lookup_customer",
        tool_call_id="tc-1",
        arguments=arguments,
        llm=None,
        pipeline_worker=worker,
        context=None,
        result_callback=result_callback,
        app_resources=None,
        worker_runner=None,
    )
    return params, results


async def test_caller_id_binding_overrides_llm_argument():
    """LLM-supplied phone must never reach the tool; verified caller_id wins."""
    tool = TenantToolConfig(
        name="lookup_customer",
        description="d",
        parameters={"phone": {"type": "string"}},
        required=["phone"],
        binding={"phone": "caller_id"},
    )
    tenant = TenantConfig(tenant_id="t", system_prompt="s", tools=[tool])
    session = make_session("+919876543210")
    settings = Settings(mock_tool_delay_ms=0)

    handler = register_tools(tenant, session, settings)["lookup_customer"]
    worker = StubWorker()
    params, results = make_params({"phone": "9999999999"}, worker)
    await handler(params)

    assert results and results[0]["ok"] is True
    assert results[0]["data"]["phone"] == "+919876543210"  # session identity, not "999..."


async def test_filler_pushed_when_tool_slow():
    tool = TenantToolConfig(name="get_order_status", description="d")
    tenant = TenantConfig(tenant_id="t", system_prompt="s", tools=[tool])
    session = make_session()
    settings = Settings(mock_tool_delay_ms=800, filler_after_ms=100)

    handler = register_tools(tenant, session, settings)["get_order_status"]
    worker = StubWorker()
    params, results = make_params({"order_id": "12345"}, worker)

    started = time.monotonic()
    await handler(params)
    elapsed = time.monotonic() - started

    assert len(worker.frames) == 1
    assert isinstance(worker.frames[0], TTSSpeakFrame)
    assert worker.frames[0].text  # a filler phrase was queued
    assert results and results[0]["ok"] is True
    assert elapsed >= 0.7  # still waited for the real result after the filler


async def test_no_filler_when_tool_fast():
    tool = TenantToolConfig(name="get_order_status", description="d")
    tenant = TenantConfig(tenant_id="t", system_prompt="s", tools=[tool])
    session = make_session()
    settings = Settings(mock_tool_delay_ms=10, filler_after_ms=5000)

    handler = register_tools(tenant, session, settings)["get_order_status"]
    worker = StubWorker()
    params, results = make_params({"order_id": "12345"}, worker)
    await handler(params)

    assert worker.frames == []
    assert results and results[0]["ok"] is True


async def test_unknown_mock_tool_returns_structured_error():
    tool = TenantToolConfig(name="mystery_tool", description="d")  # no url, not a mock tool
    tenant = TenantConfig(tenant_id="t", system_prompt="s", tools=[tool])
    session = make_session()
    settings = Settings(mock_tool_delay_ms=0)

    handler = register_tools(tenant, session, settings)["mystery_tool"]
    worker = StubWorker()
    params, results = make_params({}, worker)
    await handler(params)

    assert results and results[0]["ok"] is False
    assert results[0]["error"]["code"] == "no_endpoint"


def test_hmac_signature():
    body = b'{"tool":"x"}'
    sig = sign_payload(body, "secret")
    assert sig == hmac.new(b"secret", body, hashlib.sha256).hexdigest()


async def test_executor_structured_error_on_http_failure():
    def failing_handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    tool = TenantToolConfig(name="t", description="d", url="https://tenant.example/tool")
    transport = httpx.MockTransport(failing_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await execute_tool(
            tool,
            {"a": 1},
            call_id="call-1",
            tool_call_id="tc-1",
            timeout_secs=1.0,
            client=client,
        )
    assert result.ok is False
    assert result.error is not None
    assert "connection refused" in result.error


async def test_executor_signs_and_sends_payload():
    seen = {}

    def handler(request: httpx.Request):
        seen["signature"] = request.headers.get("X-VA-Signature")
        seen["body"] = request.content
        return httpx.Response(200, json={"status": "ok"})

    tool = TenantToolConfig(name="t", description="d", url="https://tenant.example/tool")
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await execute_tool(
            tool,
            {"a": 1},
            call_id="call-1",
            tool_call_id="tc-1",
            hmac_secret="dev-secret",
            timeout_secs=1.0,
            client=client,
        )

    assert result.ok is True
    assert result.data == {"status": "ok"}
    expected = sign_payload(seen["body"], "dev-secret")
    assert seen["signature"] == expected
    assert b'"idempotency_key":"call-1:tc-1"' in seen["body"]
