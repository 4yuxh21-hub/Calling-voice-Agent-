"""FastAPI server entrypoint.

Run:  python -m apps.server          (or: uvicorn apps.server:app --port 8080)
One WebSocket connection = one pipeline (per-call isolation).
"""

from fastapi import FastAPI
from loguru import logger

from va.config import load_settings
from va.telephony.routes import create_router


def create_app() -> FastAPI:
    settings = load_settings()
    app = FastAPI(title="Voice Agent Platform", version="0.1.0")
    app.include_router(create_router(settings))

    @app.get("/healthz")
    async def healthz():
        return {
            "ok": True,
            "mode": settings.mode,
            "mock_mode": settings.mock_mode,
            "carrier": settings.carrier,
            "sample_rate": settings.sample_rate,
        }

    logger.info(
        "Server up: mode={} mock={} carrier={} ws=/ws/{{carrier}}",
        settings.mode, settings.mock_mode, settings.carrier,
    )
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = load_settings()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
