"""Test fixtures.

Real Postgres and Redis, never mocked -- mocking the database hides
exactly the bugs Row-Level Security introduces (a query that "works" in a
mock because the mock never enforces `SET LOCAL app.actor_id` proves
nothing about the real authorization boundary).

Two ways to get there, selected automatically:

  * Default: `testcontainers` spins up ``pgvector/pgvector:pg16`` and
    ``redis:7-alpine`` for the test session. This is what CI uses.
  * If ``TEST_DATABASE_URL``/``TEST_REDIS_URL`` are set, those are used
    instead and testcontainers/Docker are never touched. This exists for
    sandboxes without registry access to pull images (see README's
    "Environment constraints" note) -- the tests and the assertions they
    make are identical either way; only how the database gets there
    differs.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import date

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

os.environ.setdefault("APP_ENV", "test")

_MIGRATOR_ROLE = "clinical_app"
_MIGRATOR_PASSWORD = "devpassword"
_RUNTIME_ROLE = "clinical_runtime"
_RUNTIME_PASSWORD = "runtimepassword"


def _to_asyncpg_url(url: str) -> str:
    # testcontainers' get_connection_url() has, across versions, returned
    # both a bare "postgresql://" and a driver-qualified
    # "postgresql+psycopg2://" -- normalize either (or any other +driver)
    # to +asyncpg so create_async_engine always gets an async-capable URL.
    return re.sub(r"^postgresql(\+\w+)?://", "postgresql+asyncpg://", url, count=1)


def _with_redis_db_index(url: str, index: int) -> str:
    """Swap a redis:// URL's trailing /<db-index> for a different one --
    used to give the FastAPI app under test its own Redis logical DB (1)
    separate from whatever DB index the base fixture URL points at (0),
    so cache/rate-limit/idempotency state never crosses test runs sharing
    one Redis instance."""
    base, _, _ = url.rpartition("/")
    return f"{base}/{index}"


async def _bootstrap_roles_and_extension(superuser_url: str) -> None:
    """Idempotently create the two-role split (see app/core/config.py) and
    the pgvector extension on a fresh testcontainers Postgres, which starts
    out with just a superuser and no application roles at all."""
    engine = create_async_engine(_to_asyncpg_url(superuser_url), isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        for role, password, extra in (
            (_MIGRATOR_ROLE, _MIGRATOR_PASSWORD, "CREATEDB"),
            (_RUNTIME_ROLE, _RUNTIME_PASSWORD, "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"),
        ):
            exists = await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})
            if exists.scalar_one_or_none() is None:
                # CREATE ROLE's PASSWORD clause is a string literal in
                # Postgres's own grammar, not a bind-parameter position --
                # asyncpg always uses the server-side extended query
                # protocol, so a $1 placeholder there is a syntax error
                # (psycopg2 never hit this because it substitutes
                # parameters client-side before sending plain SQL text).
                # role/password here are always one of the two hardcoded
                # module-level constants above, never external input.
                quoted_password = password.replace("'", "''")
                await conn.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{quoted_password}' {extra}"))

        # PostgreSQL 15+ no longer grants CREATE on the public schema to
        # every role by default. The migrator owns the application tables,
        # so it needs schema CREATE/USAGE before Alembic can create its
        # version table and the initial schema. The runtime role deliberately
        # gets only USAGE; it must never be able to create schema objects.
        await conn.execute(text("GRANT USAGE, CREATE ON SCHEMA public TO clinical_app"))
        await conn.execute(text("GRANT USAGE ON SCHEMA public TO clinical_runtime"))
    await engine.dispose()


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def postgres_url():
    external = os.environ.get("TEST_DATABASE_URL")
    if external:
        yield external
        return

    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        superuser_url = pg.get_connection_url()
        await _bootstrap_roles_and_extension(superuser_url)
        host = pg.get_container_host_ip()
        port = pg.get_exposed_port(5432)
        dbname = pg.dbname
        yield f"postgresql+asyncpg://{_RUNTIME_ROLE}:{_RUNTIME_PASSWORD}@{host}:{port}/{dbname}"


@pytest_asyncio.fixture(scope="session")
async def migrator_url(postgres_url):
    external = os.environ.get("TEST_DATABASE_URL_MIGRATOR")
    if external:
        return external
    # Swap the runtime credentials for the owner's -- same host/db, both
    # roles were created against the same database by the bootstrap above.
    return postgres_url.replace(f"{_RUNTIME_ROLE}:{_RUNTIME_PASSWORD}", f"{_MIGRATOR_ROLE}:{_MIGRATOR_PASSWORD}")


@pytest_asyncio.fixture(scope="session")
async def redis_url():
    external = os.environ.get("TEST_REDIS_URL")
    if external:
        yield external
        return

    from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as redis:
        yield f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0"


@pytest_asyncio.fixture(scope="session")
async def migrated_db(postgres_url, migrator_url):
    """Run every migration up to head once per test session, exactly as CI
    does, against the ephemeral (or externally provided) test database."""
    os.environ["DATABASE_URL"] = postgres_url
    os.environ["DATABASE_URL_MIGRATOR"] = migrator_url

    from alembic import command
    from alembic.config import Config as AlembicConfig

    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", migrator_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    yield


@pytest_asyncio.fixture
async def app_instance(migrated_db, redis_url, monkeypatch):
    """A fresh FastAPI app per test, wired to the shared test Postgres and a
    dedicated Redis DB index (1) so rate-limit/idempotency state from one
    test never bleeds into the next."""
    from app.core.config import get_settings

    monkeypatch.setenv("REDIS_URL", _with_redis_db_index(redis_url, 1))
    get_settings.cache_clear()

    from app.main import create_app

    application = create_app()
    async with application.router.lifespan_context(application):
        yield application
    get_settings.cache_clear()


@pytest_asyncio.fixture(autouse=True)
async def clean_state(migrated_db, migrator_url, redis_url):
    """Truncate every table between tests using the owner connection
    (bypasses RLS entirely, which is exactly what a test-cleanup role
    should do) and flush the test Redis DB."""
    yield
    engine = create_async_engine(_to_asyncpg_url(migrator_url))
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "TRUNCATE care_assignments, documents, audit_events, idempotency_keys, "
                "ingest_jobs, refresh_tokens, users, patients CASCADE"
            )
        )
        await conn.commit()
    await engine.dispose()

    import redis.asyncio as redis_lib

    r = redis_lib.from_url(_with_redis_db_index(redis_url, 1))
    await r.flushdb()
    await r.aclose()


@pytest_asyncio.fixture
async def client(app_instance):
    # raise_app_exceptions=False so an unhandled exception in a route goes
    # through the app's own exception middleware (app/core/errors.py) and
    # comes back as the real RFC 7807 500 response, exactly like it would
    # over a real socket -- httpx's default (True) instead re-raises the
    # exception into the test, which is useful for catching accidental
    # 500s but wrong for a test that specifically exercises the 500 path.
    transport = ASGITransport(app=app_instance, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# --- Domain fixtures -------------------------------------------------------


@pytest_asyncio.fixture
async def db_session(app_instance):
    """One transaction for the whole fixture-seeding step. Scoped as the
    `service` actor (rather than unscoped) purely so document inserts --
    RLS-restricted to that role -- can happen in the *same* transaction as
    the user/patient/assignment inserts that precede them; two separate
    transactions would mean the second one can't see the first's
    not-yet-committed rows (a real foreign-key failure, not an RLS one)."""
    from app.workers.ingest import SERVICE_ACTOR_ID

    async with app_instance.state.db.session_as(SERVICE_ACTOR_ID, "service") as session:
        yield session


async def _make_user(session, email: str, password: str, role: str, full_name: str = "Test User"):
    from app.models.user import User
    from app.security.passwords import hash_password

    user = User(id=uuid.uuid4(), email=email, hashed_password=hash_password(password), full_name=full_name, role=role)
    session.add(user)
    await session.flush()
    return user


async def _make_patient(session, external_id: str, given: str = "Given", family: str = "Family"):
    from app.models.patient import Patient

    patient = Patient(
        id=uuid.uuid4(), external_id=external_id, given_name=given, family_name=family,
        birth_date=date(1980, 1, 1), gender="female",
    )
    session.add(patient)
    await session.flush()
    return patient


async def _assign(session, clinician_id, patient_id):
    from app.models.care_assignment import CareAssignment

    session.add(CareAssignment(id=uuid.uuid4(), clinician_id=clinician_id, patient_id=patient_id))
    await session.flush()


async def _add_document(session, patient_id, chunk_id: str, text_: str, embedding: list[float] | None = None):
    from app.models.document import Document
    from app.services.embeddings import HashingEmbedder

    embedder = HashingEmbedder(dim=256)
    doc = Document(
        id=uuid.uuid4(), patient_id=patient_id, chunk_id=chunk_id, chunk_level="fixed_512",
        text=text_, embedding=embedding if embedding is not None else embedder.embed(text_),
    )
    session.add(doc)
    await session.flush()
    return doc


@pytest_asyncio.fixture
async def world(app_instance):
    """A ready-made two-patient, two-clinician world used by most tests:
    clinician_a is assigned to patient_a only, clinician_b to patient_b
    only, plus an auditor and an admin with no assignments.

    Seeded in its own session that is fully opened *and closed* (committed)
    before this fixture returns -- unlike ``db_session`` (held open for the
    whole test), because the test body's HTTP calls run through the app's
    own, separate connections/transactions and would not see this data at
    all under Postgres's normal read-committed isolation if the seeding
    transaction were still open when they ran.
    """
    from app.workers.ingest import SERVICE_ACTOR_ID

    async with app_instance.state.db.session_as(SERVICE_ACTOR_ID, "service") as session:
        clinician_a = await _make_user(session, "clinician.a@test.local", "pw-a", "clinician", "Clinician A")
        clinician_b = await _make_user(session, "clinician.b@test.local", "pw-b", "clinician", "Clinician B")
        auditor = await _make_user(session, "auditor@test.local", "pw-aud", "auditor", "Auditor")
        admin = await _make_user(session, "admin@test.local", "pw-admin", "admin", "Admin")

        patient_a = await _make_patient(session, "PatientA1", "Alice", "Anderson")
        patient_b = await _make_patient(session, "PatientB1", "Bob", "Baker")

        await _assign(session, clinician_a.id, patient_a.id)
        await _assign(session, clinician_b.id, patient_b.id)

        doc_a = await _add_document(
            session, patient_a.id, "fixed_512::a::0", "Patient A has type 2 diabetes managed with metformin."
        )
        doc_b = await _add_document(
            session, patient_b.id, "fixed_512::b::0", "Patient B was treated for a fractured wrist."
        )

    class World:
        pass

    w = World()
    w.clinician_a = clinician_a
    w.clinician_a_password = "pw-a"
    w.clinician_b = clinician_b
    w.clinician_b_password = "pw-b"
    w.auditor = auditor
    w.auditor_password = "pw-aud"
    w.admin = admin
    w.admin_password = "pw-admin"
    w.patient_a = patient_a
    w.patient_b = patient_b
    w.doc_a = doc_a
    w.doc_b = doc_b
    return w


async def login(client: AsyncClient, email: str, password: str) -> str:
    resp = await client.post("/v1/auth/token", data={"username": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
