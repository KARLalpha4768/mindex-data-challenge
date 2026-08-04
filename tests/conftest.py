"""SUPERSEDED -- the first attempt's pytest fixtures. Not part of the submitted suite.

The submitted test suite is ``solution/tests/`` -- 87 tests, including a golden
end-to-end run against the real ``data/raw/`` CSVs with every published figure pinned.
This file belongs to the repository-root ``tests/`` package, which exercised the
repository-root ``src/`` package: itself a superseded first attempt whose modules now
raise on import.

This conftest does two jobs, and neither of them is providing fixtures:

1. It stops pytest collecting anything from this directory (``collect_ignore_glob``),
   and says so in the session header, because a silently skipped directory is
   indistinguishable from one that was never there.
2. It resolves the ``tests`` package-name collision described below, so that a
   reviewer who simply types ``pytest`` at the repository root gets the 87 submitted
   tests instead of an ``ImportPathMismatchError``.

WHY ``collect_ignore_glob`` AND NOT ``pytest.skip(allow_module_level=True)``
---------------------------------------------------------------------------
A conftest is not a test module. Calling ``pytest.skip()`` here raises ``Skipped``
during conftest import, which pytest reports as an internal error and which aborts the
*whole* session, including any other directory the reviewer meant to run.
``collect_ignore_glob`` is the supported "there is nothing to collect here" hook and
leaves the rest of the session untouched. The individual ``test_*.py`` files in this
directory additionally carry their own module-level skip, so naming one directly still
produces an explanation rather than an import error.

Supported ways to run the real suite::

    python -m pytest solution/tests -q          # from the repository root
    cd solution && python -m pytest -q          # from inside the submission
    python scripts/verify_submission.py         # the whole submission, one command

Defects handled: none. This file is inert.
"""

from __future__ import annotations

import sys
from pathlib import Path

# WHY a glob rather than an explicit file list: the point is that *nothing* in this
# directory is collectable, and an explicit list would quietly start collecting again
# the day someone dropped a new file in here.
collect_ignore_glob = ["*.py"]

_HERE = Path(__file__).resolve().parent

_NOTICE = (
    "note: tests/ at the repository root is a SUPERSEDED first attempt and is not "
    "collected; the submitted suite is solution/tests/"
)


def pytest_report_header() -> str:
    """Tell the reviewer, in the session header, why this directory contributes nothing.

    Returns:
        A single line printed above the collection summary.

    WHY the header hook rather than a bare ``print``: a print at conftest-import time
    lands before pytest has configured its terminal writer and is swallowed under
    ``-q``. The header hook renders at every verbosity level.
    """
    return _NOTICE


# ── The two-``tests``-package collision ───────────────────────────────────────────
# ``solution/tests/`` and this directory are BOTH importable packages literally named
# ``tests`` (each has an ``__init__.py``, and neither parent directory does), so pytest
# derives the same dotted module name -- ``tests.conftest`` -- for both files. Whichever
# is imported first owns that slot in ``sys.modules``; the second import then fails the
# ``__file__`` cross-check with ``ImportPathMismatchError``, which aborts the entire
# session. Without the hook below, ``pytest`` typed at the repository root crashes
# during collection instead of running the submitted suite.
#
# WHY THE FIX LIVES HERE AND NOT IN ``solution/``: this is the superseded directory, so
# the entire cost of retaining the first attempt in-tree is paid in this file. Nothing
# under ``solution/`` needs to know that this directory exists.
_STALE_MODULE_NAMES = ("tests.conftest", "tests")


def pytest_plugin_registered(plugin: object) -> None:
    """Release the ``tests.*`` module names once this conftest has finished loading.

    Args:
        plugin: The plugin pytest has just registered. Every registration other than
            this module's own is ignored.

    WHY ``pytest_plugin_registered`` rather than popping at import time: ``importlib``
    re-inserts a module into ``sys.modules`` *after* its body finishes executing, so a
    pop inside the module body does not survive. This hook is the first callback that
    runs after the import has completed, and it still runs well before pytest descends
    into ``solution/tests`` -- exactly the window in which the names must be free.

    WHY ``pytest_collectstart`` does not work here: this conftest is loaded lazily, as
    a side effect of resolving ``tests.conftest`` for *solution's* file, which happens
    inside the very import that then fails. By then no collection hook gets another
    chance to fire.
    """
    if plugin is not sys.modules.get(__name__):
        return
    for name in _STALE_MODULE_NAMES:
        module = sys.modules.get(name)
        # WHY the path guard: popping unconditionally would evict solution/tests' own
        # modules the moment they register, reloading its fixtures under a second
        # module object. Only this directory's entries are released.
        if module is not None and str(getattr(module, "__file__", "")).startswith(str(_HERE)):
            del sys.modules[name]
