"""Structured (JSON, one line per event) logging. Every line that comes out
of a request carries ``request_id``, ``actor_id``, ``route``, ``status``,
and ``duration_ms`` so a support engineer can `grep` one request id across
both the API's and the worker's logs and see the whole story -- including
the async ingest failure that happened three retries after the original
HTTP call returned 202. That propagation (``request_id`` written into the
arq job at enqueue time, read back out in the worker's log lines) is set up
in app/api/v1/ingest.py and app/workers/ingest.py.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
from typing import Any

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
actor_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("actor_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "actor_id", "route", "status", "duration_ms", "job_id", "task"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)


def log_event(logger: logging.Logger, message: str, **fields: Any) -> None:
    """Attach structured fields (request_id, actor_id, route, status,
    duration_ms, ...) to a single log line without fighting `extra`'s
    reserved-keys restrictions."""
    logger.info(message, extra=fields)
