from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.db.session import Database
from app.services.embeddings import HashingEmbedder
from app.workers.ingest import process_bundle

_settings = get_settings()


async def startup(ctx: dict[str, Any]) -> None:
    ctx["settings"] = _settings
    ctx["db"] = Database(_settings)
    ctx["embedder"] = HashingEmbedder(dim=_settings.embedding_dim)


async def shutdown(ctx: dict[str, Any]) -> None:
    db: Database = ctx["db"]
    await db.dispose()


class WorkerSettings:
    functions = [process_bundle]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_tries = _settings.ingest_max_retries
    job_timeout = 300
