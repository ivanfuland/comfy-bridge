"""FastAPI app factory. CORS uses an explicit origin allowlist (spec §6): ComfyUI is on
8188, bridge on 8190 -> cross-origin; do NOT use credentials, do NOT reflect arbitrary origins."""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters import load_adapters
from app.config import load_config
from app.errors import install_exception_handlers
from app.router import router

logging.basicConfig(level=logging.INFO)


def create_app() -> FastAPI:
    cfg = load_config()
    app = FastAPI(title="comfy-bridge", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"],
        allow_headers=["*"],
    )
    install_exception_handlers(app)
    load_adapters()
    app.include_router(router)
    from app.assets import assets_router
    app.include_router(assets_router)
    from app.gating import gating_router
    app.include_router(gating_router)
    return app


app = create_app()
