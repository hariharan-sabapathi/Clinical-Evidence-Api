"""Engine/session wiring plus the actor-scoping dependency that Row-Level
Security depends on.

Every request that touches clinical data goes through ``db_session_for``,
which opens one transaction and issues ``SET LOCAL app.actor_id`` before any
other statement runs. ``SET LOCAL`` (as opposed to ``SET``) scopes the
setting to the current transaction: it is unset automatically at COMMIT or
ROLLBACK, so a connection handed back to the pool can never carry a stale
actor identity into someone else's request. See "Authorization model" in
the README and docs/adr/0001-postgres-rls-over-application-authz.md.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings


def build_engine(settings: Settings | None = None, *, url: str | None = None) -> AsyncEngine:
    settings = settings or get_settings()
    return create_async_engine(
        url or settings.database_url,
        pool_size=settings.db_pool_size,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {"statement_timeout": str(settings.db_statement_timeout_ms)},
            "timeout": settings.db_pool_timeout_seconds,
        },
    )


class Database:
    """Thin holder so tests can build an isolated instance pointed at a
    testcontainers Postgres without touching process-wide globals."""

    def __init__(self, settings: Settings | None = None, *, url: str | None = None):
        self.settings = settings or get_settings()
        self.engine = build_engine(self.settings, url=url)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def dispose(self) -> None:
        await self.engine.dispose()

    @asynccontextmanager
    async def session_as(
        self, actor_id: str | None, actor_role: str | None = None
    ) -> AsyncIterator[AsyncSession]:
        """One transaction, scoped to ``actor_id``/``actor_role`` for the
        lifetime of the `with` block. No actor (e.g. token issuance) still
        opens a transaction but skips the SET LOCAL — RLS policies then see
        NULL and every policy predicate evaluates false, which is the
        fail-closed default we want for an unauthenticated connection.

        ``SET LOCAL`` is transaction-scoped by Postgres itself, so even if
        this session/connection is reused from a pool, the setting cannot
        leak into the next transaction — that's the whole reason this isn't
        plain ``SET``."""
        async with self.session_factory() as session:
            async with session.begin():
                # set_config(..., is_local=true) is the parameterizable equivalent of
                # `SET LOCAL app.x = value` — the bare SET command doesn't accept bind
                # parameters through the extended query protocol, and building it with
                # string interpolation would reopen the SQL-injection hole RLS is
                # supposed to close.
                if actor_id is not None:
                    await session.execute(
                        text("SELECT set_config('app.actor_id', :actor, true)"), {"actor": actor_id}
                    )
                if actor_role is not None:
                    await session.execute(
                        text("SELECT set_config('app.actor_role', :role, true)"), {"role": actor_role}
                    )
                yield session

    @asynccontextmanager
    async def session_unscoped(self) -> AsyncIterator[AsyncSession]:
        """For paths with no RLS-governed table: auth, idempotency store,
        ingest job bookkeeping keyed by job id rather than patient."""
        async with self.session_factory() as session:
            async with session.begin():
                yield session
