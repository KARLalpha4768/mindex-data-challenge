"""SUPERSEDED -- the first attempt's monolithic cleaning pass. Do not read this file as the submission.

This module belongs to the repository-root ``src/`` package, which is the **first
attempt** at the Mindex data-engineering challenge. It is retained only as history.
The maintained implementation is::

    solution/src/cleaning/ (rules.py, stores.py, products.py, transactions.py)

KNOWN BUGS IN THIS FILE, kept visible rather than quietly patched:

  * It recomputed `total_amount = unit_price * quantity`. That destroyed TX-03 -- the
    20 genuinely discounted rows -- and would have published $159,005.77 instead of the
    correct $158,044.29, an overstatement of $961.48.
  * `drop_duplicates(subset=['product_id'])` swallowed PR-02, so the P005 price change
    was reported as a harmless duplicate and never surfaced.
  * Date parsing used a single `pd.to_datetime(..., errors='coerce')` call, silently
    NaT-ing the 20 TX-01 mixed-format rows.
  * `zip_code.astype(str).str.zfill(5)` ran unconditionally on every row, so ST-01
    left no trace of which ZIP had ever been malformed.
  * `state_to_region` mapped NY -> 'East' while the column's own vocabulary says
    'Northeast', inventing a fifth region that split the Northeast in two.

Importing this module raises immediately. It is deliberately **not** a transparent
re-export of the maintained code: forwarding would make the wrong import path work and
would hide the duplicated tree instead of surfacing it. See ``src/__init__.py`` for the
full list of what this attempt got wrong, and the root ``README.md`` for why the
directory is still here.

Defects handled: none. This file is inert.
"""

from __future__ import annotations

raise RuntimeError(
    "src/cleaner.py at the repository root is a SUPERSEDED first attempt and is retained "
    "only as history -- it is not the submission. The maintained implementation is "
    "solution/src/cleaning/ (rules.py, stores.py, products.py, transactions.py). "
    "Run:  cd solution && python -m src.pipeline --output-dir /tmp/run   "
    "or, from the repository root:  python scripts/verify_submission.py"
)
