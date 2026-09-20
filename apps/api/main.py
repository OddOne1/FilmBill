import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .config import settings
from .routers import (
    admin,
    auth,
    email_settings,
    events,
    files,
    health,
    notifications,
    setup,
    site_settings,
    users,
)
from .services.s3_service import ensure_bucket_exists
from .middleware.global_rate_limit import GlobalRateLimitMiddleware
from .middleware.setup_guard import SetupGuardMiddleware
from .middleware.no_cache_errors import NoCacheErrorsMiddleware

@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_bucket_exists()
    yield

_disable_docs = os.getenv("DISABLE_DOCS", "").lower() in ("true", "1", "yes")

app = FastAPI(
    title="FilmBill API",
    description="Billing, accounting and production-resource API for film, photo and media production companies",
    version="0.1.0",
    lifespan=lifespan,
    contact={"name": "FilmBill", "url": "https://github.com/OddOne1/filmbill"},
    license_info={"name": "AGPL-3.0-or-later"},
    docs_url=None if _disable_docs else "/docs",
    redoc_url=None if _disable_docs else "/redoc",
    openapi_url=None if _disable_docs else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_url,
        "http://localhost:3100",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GlobalRateLimitMiddleware)
app.add_middleware(SetupGuardMiddleware)
# Added LAST on purpose. Starlette's add_middleware inserts at the front of
# the stack, so the last one added is the OUTERMOST: this therefore sees the
# final status of every response, including the 429s the rate limiter
# returns and anything the setup guard short-circuits before a route is ever
# reached. Added earlier in this list, it would miss exactly those.
app.add_middleware(NoCacheErrorsMiddleware)

# Registered here and nowhere else. tests/test_openapi_surface.py pins the
# resulting tag set and the full path list against a committed snapshot, so
# adding a router is a deliberate, reviewable act rather than something that
# widens the public API by accident.
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(setup.router)
app.include_router(admin.router)
app.include_router(notifications.router)
app.include_router(events.router)
app.include_router(site_settings.router)
app.include_router(email_settings.router)
app.include_router(files.router)
app.include_router(health.router)
