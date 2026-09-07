from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.observability.logging import actor_id_var, log_event, request_id_var
from app.observability.metrics import http_request_duration_seconds

logger = logging.getLogger("app.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Generates (or accepts) a request id, puts it on ``request.state`` for
    the error handlers and route code to read, echoes it back on the
    response, and writes one structured access-log line per request with
    the fields the spec calls out: request_id, actor_id, route, status,
    duration_ms."""

    def __init__(self, app: ASGIApp, header_name: str = "X-Request-ID"):
        super().__init__(app)
        self.header_name = header_name

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get(self.header_name) or str(uuid.uuid4())
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        actor_token = actor_id_var.set(None)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            log_event(
                logger,
                "unhandled_exception",
                request_id=request_id,
                actor_id=actor_id_var.get(),
                route=request.url.path,
                status=500,
                duration_ms=round(duration_ms, 2),
            )
            raise
        finally:
            request_id_var.reset(token)
            actor_id_var.reset(actor_token)

        duration_ms = (time.perf_counter() - start) * 1000
        response.headers[self.header_name] = request_id
        route = request.scope.get("route")
        route_template = route.path if route is not None else request.url.path
        http_request_duration_seconds.labels(
            route=route_template, method=request.method, status=str(response.status_code)
        ).observe(duration_ms / 1000)
        log_event(
            logger,
            "request_completed",
            request_id=request_id,
            actor_id=getattr(request.state, "actor_id", None),
            route=route_template,
            status=response.status_code,
            duration_ms=round(duration_ms, 2),
        )
        return response
