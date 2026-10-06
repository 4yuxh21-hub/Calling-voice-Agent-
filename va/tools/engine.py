"""Per-tenant tool engine on top of Pipecat function calling.

At call start, tenant tool configs become `FunctionSchema`s + registered
handlers. Zero tenant tools is valid (day-1 `tools=[]`). Each handler:

1. Overrides bound args (e.g. phone) with the verified caller_id from the
   carrier - LLM-supplied values can never select sensitive targets.
2. Starts the HTTP/mock dispatch as a task and waits `filler_after_ms`; on
   timeout it queues a filler phrase and keeps waiting.
3. Returns a structured result through `params.result_callback`, so failures
   degrade to an apology path instead of a hung call.

Tenants with knowledge files (config/tenants/knowledge/<tenant_id>/) also get
an auto-registered `search_company_kb` tool that Gemini uses to answer
company-specific questions - see va/tools/kb.py.
"""

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.services.llm_service import FunctionCallParams

from va.config.settings import PROJECT_ROOT, Settings
from va.config.tenants import TenantConfig, TenantToolConfig
from va.session import CallSession
from va.tools.executor import execute_tool
from va.tools.mock_tools import MOCK_TOOLS, run_mock_tool

KB_TOOL_NAME = "search_company_kb"

KB_TOOL_CONFIG = TenantToolConfig(
    name=KB_TOOL_NAME,
    description=(
        "Search the company knowledge base for company-specific information "
        "(hours, locations, policies, products, pricing, procedures). Use this "
        "for ANY question about the company before answering from memory."
    ),
    parameters={
        "query": {
            "type": "string",
            "description": "Short search phrase, e.g. 'return policy', 'opening hours sunday'",
        }
    },
    required=["query"],
    read_only=True,
)


@dataclass
class ToolBundle:
    tools_schema: ToolsSchema | None
    handlers: dict[str, object]  # name -> async handler for llm.register_function


def kb_dir_for(tenant: TenantConfig) -> Path | None:
    """Knowledge directory for a tenant; None when the tenant has no KB files."""
    if tenant.knowledge_dir:
        p = Path(tenant.knowledge_dir)
        kb_dir = p if p.is_absolute() else PROJECT_ROOT / p
    else:
        kb_dir = PROJECT_ROOT / "config" / "tenants" / "knowledge" / tenant.tenant_id
    return kb_dir if kb_dir.exists() else None


def tenant_tools_with_kb(tenant: TenantConfig) -> list[TenantToolConfig]:
    """Tenant tools plus the KB tool when knowledge files exist (checked per call)."""
    tools = list(tenant.tools)
    if kb_dir_for(tenant) is not None and all(t.name != KB_TOOL_NAME for t in tools):
        tools.append(KB_TOOL_CONFIG)
    return tools


def build_tool_schemas(tenant: TenantConfig) -> ToolsSchema | None:
    tools = tenant_tools_with_kb(tenant)
    if not tools:
        return None
    schemas = [
        FunctionSchema(
            name=t.name,
            description=t.description,
            properties=t.parameters or {},
            required=t.required,
        )
        for t in tools
    ]
    return ToolsSchema(standard_tools=schemas)


def register_tools(
    tenant: TenantConfig,
    session: CallSession,
    settings: Settings,
) -> dict[str, object]:
    """Build one async handler per tenant tool; the caller registers them on the LLM."""
    handlers: dict[str, object] = {}
    for tool in tenant_tools_with_kb(tenant):
        handlers[tool.name] = _make_handler(tool, tenant, session, settings)

    kb_dir = kb_dir_for(tenant)
    if kb_dir is not None and KB_TOOL_NAME in handlers:
        handlers[KB_TOOL_NAME] = _make_kb_handler(kb_dir)
    return handlers


def _make_handler(
    tool: TenantToolConfig,
    tenant: TenantConfig,
    session: CallSession,
    settings: Settings,
):
    filler_after_s = settings.filler_after_ms / 1000
    timeout_s = tool.timeout_ms / 1000 if tool.timeout_ms else settings.tool_timeout_secs
    filler_phrases = tenant.filler_phrases or ["One moment..."]

    async def handler(params: FunctionCallParams):
        args = dict(params.arguments)

        # Contextual data binding: server-side identity wins over LLM output.
        for arg_name, source in tool.binding.items():
            if source == "caller_id":
                args[arg_name] = session.caller_id

        dispatch = asyncio.create_task(_dispatch(tool, args, session, settings, timeout_s))
        try:
            result = await asyncio.wait_for(asyncio.shield(dispatch), timeout=filler_after_s)
        except TimeoutError:
            phrase = filler_phrases[int(uuid.uuid4().hex, 16) % len(filler_phrases)]
            logger.info("Tool {} slower than {}ms, pushing filler", tool.name, settings.filler_after_ms)
            await params.pipeline_worker.queue_frame(TTSSpeakFrame(text=phrase))
            result = await dispatch

        await params.result_callback(result)

    return handler


def _make_kb_handler(kb_dir: Path):
    """KB search is local and instant - no filler path needed."""

    from va.tools.kb import format_results, search_dir

    async def handler(params: FunctionCallParams):
        query = str(params.arguments.get("query", "")).strip()
        if not query:
            await params.result_callback({"ok": False, "error": {"code": "bad_request",
                                            "message": "query is required"}})
            return
        hits = search_dir(kb_dir, query)
        logger.info("KB search {!r} -> {} hit(s)", query, len(hits))
        await params.result_callback(format_results(hits))

    return handler


async def _dispatch(
    tool: TenantToolConfig,
    args: dict,
    session: CallSession,
    settings: Settings,
    timeout_s: float,
) -> dict:
    try:
        if tool.url is None and tool.name in MOCK_TOOLS:
            data = await run_mock_tool(tool.name, args, settings.mock_tool_delay_ms)
            return {"ok": True, "data": data}
        if tool.url is None:
            return {"ok": False, "error": {"code": "no_endpoint",
                                           "message": f"Tool {tool.name} has no endpoint configured"}}
        result = await execute_tool(
            tool,
            args,
            call_id=session.call_id,
            tool_call_id=f"tc-{uuid.uuid4().hex[:12]}",
            hmac_secret=settings.tool_hmac_secret,
            timeout_secs=timeout_s,
        )
        if result.ok:
            return {"ok": True, "data": result.data}
        return {"ok": False, "error": {"code": "tool_error", "message": result.error}}
    except Exception as exc:  # noqa: BLE001 - structured errors keep the call alive
        logger.exception("Tool dispatch failed: {}", tool.name)
        return {"ok": False, "error": {"code": "tool_error", "message": str(exc)}}
