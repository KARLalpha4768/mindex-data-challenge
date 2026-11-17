"""The dashboard's test inventory is pytest's collection, not a pass report.

Defends the validation page. It used to render ten tests in
``tests/test_defects.py`` — a module that does not exist — each badged PASS,
under a "27 / 27" counter. Neither the files nor the count were true, and a
PASS badge on a page that never ran pytest is a result without a run.

This test re-collects the suite in a subprocess and requires
``dashboard/public/data/test_inventory.json`` to name exactly those node ids,
with no status field. Adding a test without regenerating the inventory fails
here, which is the point: the page cannot quietly keep an old list.

Regenerate with::

    python dashboard/scripts/export_test_inventory.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOLUTION = REPO / "solution"
INVENTORY = REPO / "dashboard" / "public" / "data" / "test_inventory.json"


def _collected_nodeids() -> list[str]:
    env = {key: value for key, value in os.environ.items() if key != "PYTEST_CURRENT_TEST"}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "--disable-warnings"],
        cwd=SOLUTION,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return [
        line.strip()
        for line in proc.stdout.splitlines()
        if line.strip().startswith("tests/") and "::" in line
    ]


def test_inventory_matches_pytest_collection() -> None:
    """The committed inventory is the current collection and claims no results."""
    payload = json.loads(INVENTORY.read_text(encoding="utf-8"))
    collected = _collected_nodeids()

    assert payload["command"].startswith("python -m pytest --collect-only")
    assert payload["cwd"] == "solution"
    assert payload["collected"] == len(collected)
    assert [entry["nodeid"] for entry in payload["tests"]] == collected
    for entry in payload["tests"]:
        assert "status" not in entry
        assert "passed" not in entry
        assert set(entry) == {"nodeid", "doc"}
