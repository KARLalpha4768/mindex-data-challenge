"""Execute the registered business metrics against the SQLite warehouse.

This module is the boundary between the SQL layer (``queries.py``) and the
serialization layer (``analytics.json``). Its only responsibilities are:

  1. Open a **read-only** connection to ``output/warehouse.db``.
  2. Iterate the metric registry, binding parameters from ``RunConfig``.
  3. Return a structured payload that ``pipeline.py`` writes atomically.

It does NOT own any SQL. Every query lives in ``queries.py`` with its own
documentation. This separation means the queries can be reviewed, tested, and
eventually migrated to a SQL-first tool (dbt, Dataform) without touching the
runner.

Defect codes surfaced:
  TX-03 — revenue_reconciliation proves discounts survived the pipeline.
  TX-06 — top_customers_lifetime excludes GUEST by name.
  TX-10 — every SUM(net_amount) carries the return sign, so net revenue is net.

Inputs:  ``output/warehouse.db`` (read-only).
Outputs: the analytics payload dict, also written to ``output/analytics.json``.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Any

from src.analytics.queries import METRIC_REGISTRY
from src.config import (
    AS_OF_DATE,
    RECENT_WINDOW_DAYS,
    RETURN_RATE_ALERT_THRESHOLD,
    RunConfig,
)
from src.io_utils import write_json_atomic


def _build_params(cfg: RunConfig) -> dict[str, Any]:
    """Assemble the parameter dict every query may reference.

    WHY a shared dict rather than per-query params: several queries need
    ``as_of_date`` and the window boundaries. Centralising them here means the
    values are consistent across all metrics — a query that computes the window
    differently from another would be a bug, and this makes it impossible.

    Args:
        cfg: The resolved run configuration.

    Returns:
        A flat dict safe for ``cursor.execute(sql, params)``.

    Defects handled: TX-08 indirectly — ``as_of_date`` is the same pinned date
        the cleaning layer used to identify future-dated transactions.
    """
    return {
        "as_of_date": cfg.as_of_date.isoformat(),
        "window_start": cfg.recent_window_start.isoformat(),
        "return_rate_threshold": RETURN_RATE_ALERT_THRESHOLD,
    }


def _execute_metric(
    conn: sqlite3.Connection, sql: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    """Execute one metric query and return its rows as a list of dicts.

    WHY ``conn.row_factory = sqlite3.Row`` at connection time and ``dict(row)``
    here: it gives column-name-keyed dicts without parsing the SQL, and the
    column names in queries.py are the API names, so zero renaming is needed.

    Args:
        conn: An open SQLite connection with ``row_factory = sqlite3.Row``.
        sql: The query string (from ``queries.py``).
        params: Named parameters for ``?``-style or ``:name``-style bindings.

    Returns:
        A list of dicts, one per result row.
    """
    cursor = conn.execute(sql, params)
    return [dict(row) for row in cursor.fetchall()]


def run_analytics(
    db_path: Path | str | None = None,
    as_of_date: dt.date | None = None,
    output_path: Path | str | None = None,
    config: RunConfig | None = None,
    **_extra: Any,
) -> dict[str, Any]:
    """Execute every registered metric and return the structured payload.

    The pipeline calls this via ``_resolve_callable`` and
    ``_call_with_supported_kwargs``, so the signature accepts a superset of
    possible arguments. Only ``db_path`` is strictly required; everything else
    has a documented default.

    Args:
        db_path: Path to the SQLite warehouse. Defaults to
            ``config.db_path`` or ``src.config.DB_PATH``.
        as_of_date: Reference date. Defaults to ``config.as_of_date`` or
            ``src.config.AS_OF_DATE``.
        output_path: Where to write ``analytics.json``. Defaults to
            ``config.analytics_path`` or ``src.config.ANALYTICS_PATH``.
        config: A ``RunConfig`` instance; individual kwargs override it.
        **_extra: Absorbed so ``_call_with_supported_kwargs`` never fails
            on arguments this function does not need (e.g. ``audit``).

    Returns:
        ``{"generated_at": ..., "as_of_date": ..., "metrics": {id: {
        "definition_note": ..., "row_count": ..., "rows": [...]}}}``

        The ``metrics`` sub-dict is keyed by the metric id from
        ``METRIC_REGISTRY``, making the JSON self-documenting and the
        dashboard's job trivial.

    Defects handled: TX-03, TX-06, TX-10 — see module docstring.
    """
    # ── Resolve arguments from config or defaults ─────────────────────────
    # WHY this cascade: the pipeline may pass a full RunConfig, or individual
    # kwargs, or both. Individual kwargs win because they represent an explicit
    # override (e.g. a test pointing at a temp database).
    from src.config import ANALYTICS_PATH, DB_PATH

    cfg = config or RunConfig()

    resolved_db = Path(db_path) if db_path is not None else cfg.db_path
    resolved_date = as_of_date if as_of_date is not None else cfg.as_of_date
    resolved_output = (
        Path(output_path) if output_path is not None else cfg.analytics_path
    )

    # Build a temporary config with the resolved date for param generation
    effective_cfg = RunConfig(as_of_date=resolved_date)
    params = _build_params(effective_cfg)

    # ── Open read-only connection ─────────────────────────────────────────
    # WHY read-only: analytics must not mutate the warehouse. A stray UPDATE
    # in a query would silently corrupt every subsequent run's starting state.
    # ``file:`` URI with ``?mode=ro`` makes SQLite raise on any write attempt.
    uri = f"file:{resolved_db}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row

    metrics: dict[str, Any] = {}

    try:
        for metric_id, spec in METRIC_REGISTRY.items():
            rows = _execute_metric(conn, spec["sql"], params)
            metrics[metric_id] = {
                "title": spec["title"],
                "description": spec["description"],
                "definition_note": spec["definition_note"],
                # WHY the SQL text ships with the result: the reviewer-facing
                # dashboard renders the query verbatim next to its output, so
                # the numbers and the logic that produced them are never more
                # than one glance apart.
                "sql": spec["sql"].strip(),
                # F11: ``sql_ref`` is read from the registry, not derived from the
                # metric id. The derived form pointed at a lowercase symbol that
                # does not exist in queries.py, so a reviewer following the
                # reference found nothing; and it made the reference silently
                # wrong the moment an id was renamed — which is exactly what
                # happened to ``mom_growth_by_category`` and ``aov_by_region``.
                # ``validate_registry`` now resolves each ref against the module's
                # own globals at import time, so a dangling ref cannot ship.
                "sql_ref": spec["sql_ref"],
                # WHY units ship with the result: see the vocabulary comment in
                # queries.py. The consumer must never infer scale from the
                # magnitude of a value.
                "column_units": spec["column_units"],
                "row_count": len(rows),
                "rows": rows,
            }
    finally:
        conn.close()

    # ── Assemble payload ──────────────────────────────────────────────────
    payload: dict[str, Any] = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "as_of_date": resolved_date.isoformat(),
        "metric_count": len(metrics),
        "metrics": metrics,
    }

    # WHY write here even though pipeline.py also writes: this module must be
    # runnable standalone (``python -m src.analytics.runner``), and the
    # pipeline's write is a safety net, not a replacement.
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(resolved_output, payload)

    return payload


# ── Standalone entry point ────────────────────────────────────────────────────
if __name__ == "__main__":
    from src.config import DEFAULT_RUN_CONFIG

    result = run_analytics(config=DEFAULT_RUN_CONFIG)
    print(f"[Analytics] {result['metric_count']} metrics executed.")
    for mid, mdata in result["metrics"].items():
        print(f"  {mid:<28} {mdata['row_count']:>3} rows")
    print(f"  wrote {DEFAULT_RUN_CONFIG.analytics_path}")
