"""SUPERSEDED -- run configuration: paths, AS_OF_DATE, thresholds. Do not read this file as the submission.

This module belongs to the repository-root ``src/`` package, which is the **first
attempt** at the Mindex data-engineering challenge. It is retained only as history.
The maintained implementation is::

    solution/src/config.py

The replacement is not a copy. `solution/src/config.py` additionally exposes
`RunConfig`, whose `recent_window_start` *derives* the 30-day window boundary from
`AS_OF_DATE` instead of letting each caller re-do the arithmetic.

Importing this module raises immediately. It is deliberately **not** a transparent
re-export of the maintained code: forwarding would make the wrong import path work and
would hide the duplicated tree instead of surfacing it. See ``src/__init__.py`` for the
full list of what this attempt got wrong, and the root ``README.md`` for why the
directory is still here.

Defects handled: none. This file is inert.
"""

from __future__ import annotations

raise RuntimeError(
    "src/config.py at the repository root is a SUPERSEDED first attempt and is retained "
    "only as history -- it is not the submission. The maintained implementation is "
    "solution/src/config.py. "
    "Run:  cd solution && python -m src.pipeline --output-dir /tmp/run   "
    "or, from the repository root:  python scripts/verify_submission.py"
)
