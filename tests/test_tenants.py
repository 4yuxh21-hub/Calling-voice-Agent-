"""Tenant config loading and DID routing."""

from pathlib import Path

from va.config.tenants import load_tenants, resolve_tenant
from va.tools.engine import build_tool_schemas

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_demo_tenant_loads():
    tenants = load_tenants(PROJECT_ROOT / "config" / "tenants")
    assert tenants, "demo tenant config missing"
    demo = next(t for t in tenants if t.tenant_id == "demo")
    assert demo.system_prompt
    assert demo.greeting
    assert demo.voice.provider == "cartesia"


def test_did_routing_normalizes_digits():
    tenants = load_tenants(PROJECT_ROOT / "config" / "tenants")
    demo = next(t for t in tenants if t.tenant_id == "demo")
    did = demo.dids[0]

    assert resolve_tenant(did, tenants) is demo
    assert resolve_tenant(did.replace("+91", ""), tenants) is demo
    assert resolve_tenant("tel:+919876543210", tenants) is None
    assert resolve_tenant(None, tenants) is None


def test_tool_schema_build_and_empty_case():
    tenants = load_tenants(PROJECT_ROOT / "config" / "tenants")
    demo = next(t for t in tenants if t.tenant_id == "demo")

    schema = build_tool_schemas(demo)
    assert schema is not None
    names = [s.name for s in schema.standard_tools]
    # get_order_status + lookup_customer from config, plus the KB tool
    # auto-registered because the demo tenant has knowledge files
    assert names[0] == "get_order_status"
    assert "search_company_kb" in names

    empty_tenant = type(demo)(tenant_id="x", system_prompt="s")
    assert build_tool_schemas(empty_tenant) is None
