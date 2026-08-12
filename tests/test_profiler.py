"""SUPERSEDED -- profiling tests from the first attempt. Not the submitted suite.

The maintained test suite is `solution/tests/` (87 tests). This file belongs to the
repository-root `tests/` package, which tested the repository-root `src/` package --
itself a superseded first attempt whose modules now raise on import.

The maintained version additionally pins the future-date count against `AS_OF_DATE` rather
than wall-clock time, so the test does not change its own answer as the calendar moves.

This module skips at collection time rather than failing, so `pytest` run from the
wrong directory reports a readable reason instead of a stack trace. Run the real suite
with::

    cd solution && python -m pytest -q

or, from the repository root, the single command that verifies everything::

    python scripts/verify_submission.py

Defects handled: none. This file is inert.
"""

from __future__ import annotations

import pytest

# WHY allow_module_level=True: the skip has to fire during collection, before pytest
# tries to import the fixtures and helpers this file used to define. A skip inside a
# test function would be too late -- module-level import of the superseded `src`
# package raises, and the reviewer would see that traceback instead of this message.
pytest.skip(
    "SUPERSEDED: tests/test_profiler.py at the repository root belongs to the first attempt. "
    "The maintained suite is solution/tests/ -- run:  cd solution && python -m pytest -q "
    "(or, from the repository root:  python scripts/verify_submission.py)",
    allow_module_level=True,
)
