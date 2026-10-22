from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis
from starlette.middleware.cors import CORSMiddleware

from flagman.core.config import Settings, load_settings
from flagman.core.db import create_engine, create_session_factory
from flagman.core.errors import (
    PROBLEM_RESPONSES,
    register_error_handlers,
    use_problem_media_type,
)
from flagman.core.logging import configure_logging
from flagman.core.middleware import (
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from flagman.core.resources import Resources
from flagman.health.api import router as health_router
from flagman.identity.api import router as identity_router

DOCS_URL = "/docs"
SWAGGER_OAUTH_REDIRECT_URL = "/docs/oauth2-redirect"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.api_log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        redis = Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password.get_secret_value(),
            socket_connect_timeout=settings.readiness_timeout_seconds,
            socket_timeout=settings.readiness_timeout_seconds,
        )
        app.state.resources = Resources(
            settings=settings,
            engine=engine,
            session_factory=create_session_factory(engine),
            redis=redis,
        )
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()

    docs_enabled = not settings.is_prod
    app = FastAPI(
        title="FLAGMAN Drive API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=DOCS_URL if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
        swagger_ui_oauth2_redirect_url=SWAGGER_OAUTH_REDIRECT_URL,
        responses=PROBLEM_RESPONSES,
    )
    register_error_handlers(app)
    use_problem_media_type(app)

    # Starlette runs the last added middleware first, so the order of the stack is
    # SecurityHeaders -> RequestContext -> CORS -> routes.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts=settings.is_prod,
        csp_exempt_paths=(DOCS_URL, SWAGGER_OAUTH_REDIRECT_URL) if docs_enabled else (),
    )

    app.include_router(health_router)
    app.include_router(identity_router)
    return app
