#!/usr/bin/env python3
"""Write the dashboard's test inventory from ``pytest --collect-only``.

The validation page used to render a handwritten list of tests, several of
them in modules that do not exist, each marked PASS. This script records
what pytest actually collects and the first line of each test's docstring.
It does not run the tests and it does not write a status.

Run from anywhere::

    python dashboard/scripts/export_test_inventory.py

Output: ``dashboard/public/data/test_inventory.json``
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOLUTION = REPO / "solution"
OUTPUT = REPO / "dashboard" / "public" / "data" / "test_inventory.json"
COMMAND = "python -m pytest --collect-only --disable-warnings"


def _first_doc_line(node: ast.AST) -> str | None:
    doc = ast.get_docstring(node)
    if not doc:
        return None
    line = doc.strip().splitlines()[0].strip()
    return line or None


def _doc_index(path: Path) -> dict[str, str | None]:
    """Map ``Class::test_name`` and bare ``test_name`` to a docstring line."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    index: dict[str, str | None] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                is_test = isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                if is_test and item.name.startswith("test"):
                    index[f"{node.name}::{item.name}"] = _first_doc_line(item)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name.startswith("test"):
                index[node.name] = _first_doc_line(node)
    return index


def _nodeids() -> list[str]:
    env = {key: value for key, value in os.environ.items() if key != "PYTEST_CURRENT_TEST"}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "--disable-warnings"],
        cwd=SOLUTION,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(proc.returncode)
    nodeids = [
        line.strip()
        for line in proc.stdout.splitlines()
        if line.strip().startswith("tests/") and "::" in line
    ]
    if not nodeids:
        sys.stderr.write(proc.stdout)
        sys.stderr.write("pytest collected no tests\n")
        raise SystemExit(1)
    return nodeids


def _doc_for(nodeid: str, indexes: dict[str, dict[str, str | None]]) -> str | None:
    file_part, _, rest = nodeid.partition("::")
    key = rest.split("[", 1)[0]
    return indexes.get(file_part, {}).get(key)


def build_inventory() -> dict:
    nodeids = _nodeids()
    indexes: dict[str, dict[str, str | None]] = {}
    for nodeid in nodeids:
        relative = nodeid.split("::", 1)[0]
        if relative not in indexes:
            indexes[relative] = _doc_index(SOLUTION / relative)
    tests = [{"nodeid": nodeid, "doc": _doc_for(nodeid, indexes)} for nodeid in nodeids]
    return {
        "command": COMMAND,
        "cwd": "solution",
        "collected": len(tests),
        "tests": tests,
    }


def main() -> None:
    inventory = build_inventory()
    OUTPUT.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO)} ({inventory['collected']} collected)")


if __name__ == "__main__":
    main()
