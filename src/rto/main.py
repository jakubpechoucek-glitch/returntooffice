"""ASGI application factory.

Run locally::

    uvicorn rto.main:app --reload

In containers the app is served by gunicorn with uvicorn workers (see ``Dockerfile``).
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
    multiprocess,
)
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from rto import __version__
from rto.api.v1 import router as api_router
from rto.auth.deps import CsrfError, NotAuthenticated
from rto.auth.routes import router as auth_router
from rto.config import get_settings
from rto.db import get_sessionmaker
from rto.services import ServiceError
from rto.web.admin import router as admin_router
from rto.web.pages import router as pages_router
from rto.web.templating import render

REQUESTS = Counter("rto_http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("rto_http_request_seconds", "HTTP request latency", ["method", "route"])


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("request_id", "path", "status", "duration_ms", "user_id"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> None:
    s = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if s.environment != "development" else logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(s.log_level.upper())


def _is_api(request: Request) -> bool:
    return request.url.path.startswith("/api/")


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging()
    app = FastAPI(
        title="Onsite Compliance (WOS) API",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    access_log = logging.getLogger("rto.access")

    @app.middleware("http")
    async def observability(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        route = request.scope.get("route")
        route_name = getattr(route, "path", "unmatched")
        REQUESTS.labels(request.method, route_name, response.status_code).inc()
        LATENCY.labels(request.method, route_name).observe(elapsed)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if not request.url.path.startswith("/api/docs"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                "frame-ancestors 'none'; form-action 'self' https://login.microsoftonline.com; base-uri 'self'"
            )
        if s.session_https_only:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if not request.url.path.startswith(("/static", "/healthz", "/readyz", "/metrics")):
            access_log.info(
                "%s %s", request.method, request.url.path,
                extra={"request_id": rid, "path": request.url.path, "status": response.status_code,
                       "duration_ms": round(elapsed * 1000, 1)},
            )
        return response

    @app.middleware("http")
    async def demo_gate(request: Request, call_next):
        # Registered before SessionMiddleware, so it runs inside it and can read the session.
        if s.demo_password and not request.url.path.startswith(
            ("/demo-gate", "/static/", "/healthz", "/readyz")
        ) and not request.session.get("demo_ok"):
            if _is_api(request):
                return JSONResponse({"detail": "Demo password required"}, status_code=401)
            return RedirectResponse(f"/demo-gate?next={request.url.path}", status_code=303)
        return await call_next(request)

    app.add_middleware(
        SessionMiddleware,
        secret_key=s.secret_key,
        session_cookie=s.session_cookie_name,
        max_age=s.session_max_age_seconds,
        https_only=s.session_https_only,
        same_site="lax",
    )

    @app.exception_handler(NotAuthenticated)
    async def _unauth(request: Request, exc: NotAuthenticated):
        if _is_api(request):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={nxt}", status_code=303)

    @app.exception_handler(CsrfError)
    async def _csrf(request: Request, exc: CsrfError):
        if _is_api(request):
            return JSONResponse({"detail": "CSRF token missing or invalid"}, status_code=403)
        return render(request, "error.html", status_code=403, title="Session expired",
                      message="Your form has expired. Please go back, reload the page and try again.")

    @app.exception_handler(ServiceError)
    async def _service(request: Request, exc: ServiceError):
        if _is_api(request):
            return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)
        return render(request, "error.html", status_code=exc.status_code, title="Something is not right", message=str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        if _is_api(request):
            return JSONResponse({"detail": exc.errors()}, status_code=422)
        return render(request, "error.html", status_code=422, title="Invalid input",
                      message="Some fields are missing or have an invalid value.")

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return PlainTextResponse("ok")

    @app.get("/readyz", include_in_schema=False)
    def readyz():
        try:
            with get_sessionmaker()() as db:
                db.execute(text("SELECT 1"))
        except Exception:
            logging.getLogger(__name__).exception("readiness check failed")
            return PlainTextResponse("database unavailable", status_code=503)
        return PlainTextResponse("ready")

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        # With several gunicorn workers, PROMETHEUS_MULTIPROC_DIR aggregates all processes.
        if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry)
            return PlainTextResponse(generate_latest(registry).decode(), media_type=CONTENT_TYPE_LATEST)
        return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)

    app.mount("/static", StaticFiles(directory=str(pathlib.Path(__file__).parent / "web" / "static")), name="static")
    app.include_router(auth_router)
    app.include_router(api_router)
    app.include_router(admin_router)
    app.include_router(pages_router)
    return app


app = create_app()
