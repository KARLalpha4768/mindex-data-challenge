"""SUPERSEDED -- the repository-root ``tests/`` package is the first attempt's suite.

The submitted test suite is ``solution/tests/`` -- 87 tests, including a golden
end-to-end run against the real ``data/raw/`` CSVs with the published figures pinned.

Every module in this package skips at collection with a message pointing at the right
path. The package is retained only as history; see the root ``README.md`` section
"Why ``src/`` and ``tests/`` still exist at the root" for why it was not deleted.

WHY THIS ``__init__`` DOES NOT ITSELF SKIP: pytest imports the package before the test
modules, and a module-level skip here would abort collection of the directory with no
per-file explanation. Each module carries its own skip instead, so a reviewer who runs
a single file still gets the message.
"""
