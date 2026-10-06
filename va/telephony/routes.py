"""FastAPI routes: carrier WebSocket media streams + tenant routing."""

import json
from urllib.parse import unquote

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from va.bot import run_bot
from va.config.settings import Settings
from va.config.tenants import load_tenants, resolve_tenant
from va.mocks.scenarios import load_script_transcripts
from va.session import parse_caller_id


def create_router(settings: Settings) -> APIRouter:
    router = APIRouter()
    tenants = load_tenants(settings.tenants_path)

    @router.websocket("/ws/{carrier}")
    async def media_stream(websocket: WebSocket, carrier: str):
        await websocket.accept()

        # Carriers differ in what arrives first: Exotel opens with a
        # `connected` event before `start`; Twilio-style flows start directly.
        # Accept leading non-start events and locate `start` wherever it is.
        start_msg: dict | None = None
        try:
            for _ in range(10):  # bounded: a client that never says `start` gets dropped
                raw = await websocket.receive_text()
                msg = json.loads(raw)
                if msg.get("event") == "start":
                    start_msg = msg
                    break
                logger.info("WS pre-start event: {}", raw[:300])
        except WebSocketDisconnect:
            return
        except Exception as exc:  # noqa: BLE001
            logger.error("Bad WS message while waiting for start: {}", exc)
            await websocket.close(code=1002)
            return

        if start_msg is None:
            logger.error("WS closed without a 'start' event")
            await websocket.close(code=1002)
            return

        logger.info("WS start event: {}", json.dumps(start_msg)[:500])
        start = start_msg.get("start") or start_msg.get("data") or {}
        custom = start.get("customParameters") or start.get("custom_parameters") or {}
        if isinstance(custom, list):  # Twilio-style [{name, value}] variant
            custom = {item.get("name"): item.get("value") for item in custom}

        if carrier == "exotel":
            stream_sid = (
                start_msg.get("streamSid")
                or start.get("streamSid")
                or start.get("stream_sid")
                or "unknown"
            )
            call_sid = start.get("callSid") or start.get("call_sid")
        elif carrier == "telnyx":
            stream_sid = start.get("stream_id") or start_msg.get("stream_id") or "unknown"
            call_sid = start.get("call_control_id")
        else:
            logger.error("Unknown carrier on WS route: {}", carrier)
            await websocket.close(code=1008)
            return

        # Tenant routing: WS query params win, then carrier custom parameters.
        qp = websocket.query_params
        did = qp.get("did") or custom.get("did") or custom.get("CallTo")
        tenant = resolve_tenant(did, tenants)
        if tenant is None:
            tenant = tenants[0] if tenants else None
            if tenant is None:
                logger.error("No tenants configured and no DID match for {}", did)
                await websocket.close(code=1008)
                return
            logger.warning("No tenant for DID {!r}; falling back to {}", did, tenant.tenant_id)

        caller_id = unquote(qp.get("caller") or parse_caller_id(custom, start))
        script = qp.get("script") or custom.get("script")
        script_transcripts = load_script_transcripts(script)

        logger.info(
            "WS connected: carrier={} stream={} did={} tenant={} script={}",
            carrier, stream_sid, did, tenant.tenant_id, script or "-",
        )
        try:
            await run_bot(
                websocket,
                carrier=carrier,
                stream_sid=stream_sid,
                call_sid=call_sid,
                tenant=tenant,
                caller_id=caller_id,
                script_transcripts=script_transcripts,
                settings=settings,
            )
        except Exception:
            logger.exception("Bot crashed")
            await websocket.close(code=1011)

    return router
