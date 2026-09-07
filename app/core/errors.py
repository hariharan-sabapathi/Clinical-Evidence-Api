"""RFC 7807 (``application/problem+json``) everywhere. One error shape for
every non-2xx response in this service: a 404, a 403, a 422, and an
unhandled 500 all come back with the same five fields plus a request id,
so client error-handling code branches on ``status``/``type``, never on
"is this response JSON or a plain string" the way a mix of FastAPI's
default validation errors and ad-hoc ``HTTPException`` bodies would force.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"

_TITLES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    409: "Conflict",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


class ApiError(Exception):
    """Raise this (or a subclass) anywhere in application code that needs to
    return a specific RFC 7807 problem. Prefer this over ``HTTPException``
    in new code so ``type``/``detail`` are set deliberately rather than
    defaulted."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        type_: str = "about:blank",
        title: str | None = None,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.status_code = status_code
        self.detail = detail
        self.type_ = type_
        self.title = title or _TITLES.get(status_code, "Error")
        self.extra = extra or {}
        self.headers = headers or {}
        super().__init__(detail)


def _problem_response(request: Request, status_code: int, title: str, detail: str, type_: str = "about:blank",
                       extra: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    # `instance` is the route *template* ("/v1/patients/{patient_id}"), not
    # the resolved path -- a resolved path on a 403/404 would echo the
    # patient/document id straight back into the response body, which is
    # exactly the kind of incidental leak tests/security/
    # test_cross_patient_isolation.py checks for. request_id is what a
    # server-side log correlates against if the concrete path is ever
    # needed for debugging.
    route = request.scope.get("route")
    instance = route.path if route is not None else str(request.url.path)
    body = {
        "type": type_,
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": instance,
        "request_id": request_id,
    }
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status_code, content=body, media_type=PROBLEM_JSON, headers=headers or {})


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return _problem_response(request, exc.status_code, exc.title, exc.detail, exc.type_, exc.extra, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return _problem_response(request, exc.status_code, _TITLES.get(exc.status_code, "Error"), detail)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem_response(
            request,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            _TITLES[422],
            "Request validation failed.",
            extra={"errors": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Never leak internals (stack traces, exception message) into the
        # response body -- that's logged server-side with the request id
        # attached (see app/core/logging.py) and correlated by request_id.
        return _problem_response(
            request, status.HTTP_500_INTERNAL_SERVER_ERROR, _TITLES[500], "An unexpected error occurred."
        )
