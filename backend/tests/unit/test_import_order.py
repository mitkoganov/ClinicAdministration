"""Regression guard for the base<->models import-cycle risk: `app/db/base.py`
must not import `app.models` for a side effect, because every
`app/models/*.py` module imports `Base` from that same file. Within a
pytest session, `conftest.py` (or `app.main`, imported by many fixtures)
happens to import things in an order that hides a cycle - these tests spawn
a genuinely fresh interpreter so import order is never masked by whatever
already ran earlier in the same process."""

import subprocess
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]


def _run_fresh_import(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=_BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_tenant_context_imports_cleanly_in_a_fresh_interpreter():
    # Imports app.models.membership before anything else has touched
    # app.db.base - exactly the order that would raise
    # "cannot import name ... from partially initialized module 'app.models'"
    # if app/db/base.py still imported app.models for a side effect.
    result = _run_fresh_import("import app.core.tenant_context")
    assert result.returncode == 0, result.stderr


def test_main_app_imports_and_creates_cleanly_in_a_fresh_interpreter():
    result = _run_fresh_import("from app.main import create_app; create_app()")
    assert result.returncode == 0, result.stderr
