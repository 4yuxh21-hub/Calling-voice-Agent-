"""Per-tenant configuration: prompts, voices, tools, DID routing."""

import json
from pathlib import Path

from pydantic import BaseModel, Field


class TenantToolConfig(BaseModel):
    name: str
    description: str
    # JSON-schema style: {"order_id": {"type": "string", "description": "..."}}
    parameters: dict[str, dict] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)
    # External endpoint to call; None -> built-in mock tool implementation
    url: str | None = None
    timeout_ms: int | None = None
    read_only: bool = True
    # Contextual data binding: arg name -> "caller_id". Bound args are always
    # overwritten server-side from the verified caller identity, never from LLM
    # output (anti prompt-injection).
    binding: dict[str, str] = Field(default_factory=dict)


class VoiceConfig(BaseModel):
    provider: str = "cartesia"
    voice_id: str = ""
    model: str | None = None


class TenantConfig(BaseModel):
    tenant_id: str
    dids: list[str] = Field(default_factory=list)
    display_name: str = ""
    system_prompt: str
    language: str = "multi"
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    greeting: str = "Hello! How can I help you today?"
    filler_phrases: list[str] = Field(
        default_factory=lambda: ["One moment...", "Let me check that for you..."]
    )
    tools: list[TenantToolConfig] = Field(default_factory=list)
    # Optional override for the knowledge-base directory; defaults to
    # config/tenants/knowledge/<tenant_id>/. Presence of files auto-registers
    # the search_company_kb tool for this tenant.
    knowledge_dir: str | None = None


def _normalize_did(did: str) -> str:
    digits = "".join(c for c in did if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


def load_tenants(tenants_dir: Path) -> list[TenantConfig]:
    tenants: list[TenantConfig] = []
    if not tenants_dir.exists():
        return tenants
    for path in sorted(tenants_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        tenants.append(TenantConfig.model_validate(data))
    return tenants


def resolve_tenant(did: str | None, tenants: list[TenantConfig]) -> TenantConfig | None:
    """Route an inbound call to a tenant by the called DID (last 10 digits)."""
    if not did:
        return None
    normalized = _normalize_did(did)
    for tenant in tenants:
        if any(_normalize_did(d) == normalized for d in tenant.dids):
            return tenant
    return None
