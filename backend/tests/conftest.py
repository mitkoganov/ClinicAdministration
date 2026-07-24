import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - registers every model against Base.metadata before create_all/drop_all
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from tests.db_safety import get_test_database_url

pytest_plugins = ["tests.factories", "tests.auth_factories", "tests.calendar_factories"]


@pytest.fixture(scope="session")
def db_engine():
    """A real Postgres engine for the test suite — a disposable, explicitly
    test-only database (see tests/db_safety.py and README.md). Tables are
    created once per test session and dropped at the end; per-test isolation
    is handled by `db_session` below, not by recreating the schema per test.

    get_test_database_url() raises tests.db_safety.UnsafeTestDatabaseError
    (and this fixture makes no attempt to catch or fall back) before any
    engine is created or any destructive command runs, unless the target is
    conclusively a dedicated test database (test-like name AND an explicit
    ALLOW_DESTRUCTIVE_TEST_DB_RESET=true opt-in) that is not the app's own
    DATABASE_URL."""
    test_database_url = get_test_database_url()
    engine = create_engine(test_database_url, future=True)
    # `create_all()` builds the schema directly from ORM metadata, bypassing
    # Alembic migrations entirely - it never runs the migration's own
    # `CREATE EXTENSION IF NOT EXISTS btree_gist` (see
    # alembic/versions/00e7f6cca017_*.py), which the appointments table's
    # GiST exclusion constraints require. Without this, a genuinely fresh
    # test database (e.g. a freshly (re)started tmpfs-backed postgres-test
    # container) fails with "data type uuid has no default operator class
    # for access method gist" the first time any test touches Appointment.
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """Wraps each test in an outer transaction that is always rolled back,
    using SQLAlchemy's documented "join a session into an external
    transaction" recipe: application code (including code under test that
    calls `session.commit()`) only ever commits an inner SAVEPOINT, which is
    immediately restarted, so nothing survives past the end of the test."""
    connection = db_engine.connect()
    outer_transaction = connection.begin()
    session = Session(bind=connection)
    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, transaction):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    try:
        yield session
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()


@pytest.fixture
def app(db_session):
    application = create_app()

    def _override_get_db():
        yield db_session

    application.dependency_overrides[get_db] = _override_get_db
    return application


@pytest.fixture
def client(app):
    return TestClient(app)
