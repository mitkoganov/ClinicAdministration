# Quality gate run

## backend-ruff - PASS (exit 0)

```
All checks passed!

```

## backend-mypy - PASS (exit 0)

```
Success: no issues found in 13 source files

```

## backend-pytest - PASS (exit 0)

```
============================= test session starts =============================
platform win32 -- Python 3.13.7, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\m.ganov\Projects\clinic-setup\clinic-admin-platform\backend
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.14.2, asyncio-1.4.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 9 items

tests\test_app.py ..                                                     [ 22%]
tests\test_config.py ....                                                [ 66%]
tests\test_ready.py ...                                                  [100%]

============================== warnings summary ===============================
.venv\Lib\site-packages\fastapi\testclient.py:1
  C:\Users\m.ganov\Projects\clinic-setup\clinic-admin-platform\backend\.venv\Lib\site-packages\fastapi\testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================== 9 passed, 1 warning in 0.15s =========================

```

## frontend-lint - PASS (exit 0)

```

> frontend@0.1.0 lint
> eslint


```

## frontend-typecheck - PASS (exit 0)

```

> frontend@0.1.0 typecheck
> tsc --noEmit


```

## frontend-build - PASS (exit 0)

```

> frontend@0.1.0 build
> next build

▲ Next.js 16.2.10 (Turbopack)

  Creating an optimized production build ...
✓ Compiled successfully in 1152ms
  Running TypeScript ...
  Finished TypeScript in 1226ms ...
  Collecting page data using 5 workers ...
  Generating static pages using 5 workers (0/4) ...
  Generating static pages using 5 workers (1/4) 
  Generating static pages using 5 workers (2/4) 
  Generating static pages using 5 workers (3/4) 
✓ Generating static pages using 5 workers (4/4) in 445ms
  Finalizing page optimization ...

Route (app)
┌ ○ /
└ ○ /_not-found


○  (Static)  prerendered as static content


```

