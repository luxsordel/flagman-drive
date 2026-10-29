from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from redis.asyncio import Redis
from starlette.middleware.cors import CORSMiddleware

from flagman.audit.api import router as audit_router
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
from flagman.fleet.api import map_router as map_cars_router
from flagman.fleet.api import router as fleet_router
from flagman.fleet.api import staff_router as fleet_staff_router
from flagman.geo.api import map_router as map_zones_router
from flagman.geo.api import router as geo_router
from flagman.health.api import router as health_router
from flagman.identity.api import profile_router, staff_admin_router, staff_router
from flagman.identity.api import router as identity_router
from flagman.kyc.api import review_router as kyc_review_router
from flagman.kyc.api import router as kyc_router
from flagman.payments.api import router as payments_router
from flagman.payments.api import webhook_router as payments_webhook_router
from flagman.payments.fake_pages import router as fake_provider_router
from flagman.pricing.api import router as pricing_router
from flagman.rentals.api import router as rentals_router
from flagman.support.api import router as support_router
from flagman.support.api import staff_router as support_staff_router

logger = structlog.get_logger(__name__)

DOCS_URL = "/docs"
SWAGGER_OAUTH_REDIRECT_URL = "/docs/oauth2-redirect"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.api_log_level)
    # Every protection that only a public server turns on goes here, so the warning below never
    # misses one.
    prod_protections = {"hsts": settings.is_public, "hidden_api_docs": settings.is_public}
    disabled = [name for name, enabled in prod_protections.items() if not enabled]
    if disabled:
        # Loud on purpose: a server started without API_ENV=prod or stand works, but without these.
        logger.warning(
            "api_not_in_prod_mode", api_env=settings.api_env, disabled_protections=disabled
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        redis = Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password.get_secret_value(),
            socket_connect_timeout=settings.redis_timeout_seconds,
            socket_timeout=settings.redis_timeout_seconds,
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

    docs_enabled = not prod_protections["hidden_api_docs"]
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
        # Retry-After is not CORS-safelisted: without this the web client cannot read 429 timing.
        expose_headers=[REQUEST_ID_HEADER, "Retry-After"],
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts=prod_protections["hsts"],
        csp_exempt_paths=(DOCS_URL, SWAGGER_OAUTH_REDIRECT_URL) if docs_enabled else (),
    )

    app.include_router(health_router)
    app.include_router(identity_router)
    app.include_router(staff_router)
    app.include_router(staff_admin_router)
    app.include_router(profile_router)
    app.include_router(audit_router)
    app.include_router(kyc_router)
    app.include_router(kyc_review_router)
    app.include_router(fleet_router)
    app.include_router(fleet_staff_router)
    app.include_router(map_cars_router)
    app.include_router(geo_router)
    app.include_router(map_zones_router)
    app.include_router(rentals_router)
    app.include_router(pricing_router)
    app.include_router(payments_router)
    app.include_router(payments_webhook_router)
    app.include_router(support_router)
    app.include_router(support_staff_router)
    app.include_router(fake_provider_router)
    return app
