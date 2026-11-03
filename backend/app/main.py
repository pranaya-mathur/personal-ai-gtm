from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.commercial import router as commercial_router
from app.api.routes.core import public_router, router
from app.api.routes.ops import router as ops_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

log = get_logger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="Personal AI GTM Command Center", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if settings.auth_disabled:
        log.warning("auth_disabled", note="AUTH_DISABLED=true: every request is admin")
    app.include_router(public_router)
    app.include_router(router)
    app.include_router(commercial_router)
    app.include_router(ops_router)
    return app


app = create_app()
