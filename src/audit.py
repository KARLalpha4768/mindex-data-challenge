"""SUPERSEDED -- the decision ledger and the defect-coverage proof. Do not read this file as the submission.

This module belongs to the repository-root ``src/`` package, which is the **first
attempt** at the Mindex data-engineering challenge. It is retained only as history.
The maintained implementation is::

    solution/src/audit.py

`AuditLog.assert_all_expected_defects_found()` is what turns the coverage claim into
a build gate. The maintained copy is the one the pipeline and the tests exercise.

Importing this module raises immediately. It is deliberately **not** a transparent
re-export of the maintained code: forwarding would make the wrong import path work and
would hide the duplicated tree instead of surfacing it. See ``src/__init__.py`` for the
full list of what this attempt got wrong, and the root ``README.md`` for why the
directory is still here.

Defects handled: none. This file is inert.
"""

from __future__ import annotations

raise RuntimeError(
    "src/audit.py at the repository root is a SUPERSEDED first attempt and is retained "
    "only as history -- it is not the submission. The maintained implementation is "
    "solution/src/audit.py. "
    "Run:  cd solution && python -m src.pipeline --output-dir /tmp/run   "
    "or, from the repository root:  python scripts/verify_submission.py"
)
