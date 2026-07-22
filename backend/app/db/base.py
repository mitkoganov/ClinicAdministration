from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models.

    Deliberately a leaf module: it must NOT import `app.models` (or
    anything that imports a model module) here, even for the side effect of
    populating `Base.metadata`. Every `app/models/*.py` module imports
    `Base` from this file, so importing the `app.models` package back from
    here creates `base -> models -> (a model module) -> base` - Python
    would re-enter this same module mid-import. Whether that actually
    raises depends on unrelated details elsewhere (e.g. where `__all__` is
    declared in `app/models/__init__.py`), which makes it a fragile,
    accident-prone cycle rather than a safe one.

    Anything that needs `Base.metadata` to contain every table up front
    (Alembic autogenerate, test schema setup) must import `app.models`
    itself, explicitly, at its own call site - see `backend/alembic/env.py`
    and `backend/tests/conftest.py`. The application itself needs no such
    bulk import: every model class registers with `Base` as a normal
    consequence of the API/service/repository layers importing their own
    model modules while the app starts up.
    """
