"""Knowledge base: chunking, search, and auto-registration of the KB tool."""

from pathlib import Path

from va.config.settings import Settings
from va.config.tenants import TenantConfig
from va.session import CallSession
from va.tools.engine import KB_TOOL_NAME, build_tool_schemas, kb_dir_for, register_tools
from va.tools.kb import format_results, load_chunks, search

KB_DIR = PROJECT_ROOT_PATH = Path(__file__).resolve().parents[1] / "config" / "tenants" / "knowledge" / "demo"


def test_demo_kb_chunks_load():
    chunks = load_chunks(KB_DIR)
    assert len(chunks) >= 6
    headings = {c.heading for c in chunks}
    assert "Return and refund policy" in headings


def test_search_finds_relevant_chunks():
    chunks = load_chunks(KB_DIR)
    hits = search(chunks, "return policy refund")
    assert hits
    assert "refund" in hits[0].text.lower() or "return" in hits[0].text.lower()

    hits = search(chunks, "opening hours sunday")
    assert hits
    assert "Sunday" in " ".join(h.text for h in hits) or "hours" in hits[0].heading.lower()


def test_search_no_match_returns_empty():
    chunks = load_chunks(KB_DIR)
    hits = search(chunks, "quantum flux capacitor repair")
    assert hits == []
    formatted = format_results([])
    assert formatted["results"] == [] and "hint" in formatted


def test_kb_tool_auto_registers_for_tenants_with_files():
    demo = TenantConfig(tenant_id="demo", system_prompt="s")
    assert kb_dir_for(demo) == KB_DIR
    schema = build_tool_schemas(demo)
    assert KB_TOOL_NAME in [s.name for s in schema.standard_tools]


def test_kb_tool_absent_for_tenant_without_files(tmp_path):
    tenant = TenantConfig(tenant_id="ghost", system_prompt="s",
                          knowledge_dir=str(tmp_path / "nope"))
    assert kb_dir_for(tenant) is None
    assert build_tool_schemas(tenant) is None


async def test_kb_handler_returns_results():
    demo = TenantConfig(tenant_id="demo", system_prompt="s")
    session = CallSession(
        call_id="c", carrier="exotel", stream_sid="s", call_sid="c", tenant=demo,
    )
    handlers = register_tools(demo, session, Settings(mock_tool_delay_ms=0))
    assert KB_TOOL_NAME in handlers

    results = []

    async def cb(result, **kwargs):
        results.append(result)

    class P:
        arguments = {"query": "how long does shipping take"}
        async def result_callback(self, result, **kwargs):
            await cb(result)

    await handlers[KB_TOOL_NAME](P())
    assert results and results[0]["ok"] is True
    assert any("working days" in r["text"] for r in results[0]["results"])
