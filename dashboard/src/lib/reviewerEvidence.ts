/**
 * Evidence a reviewer can download or read on the validation page.
 *
 * Both of those surfaces used to ship a second, invented dataset. The export
 * modal offered "Production Ready" warehouse CSVs whose stores included
 * Crossgates Mall and whose quarantine file cited transaction ids that are
 * not in the extract. The validation page reported "27 / 27" and ten tests
 * in `tests/test_defects.py`, a module that does not exist, including an
 * ST-01 decision (keep the raw ZIP `0938`) that contradicts the pipeline.
 *
 * A download is worse than a wrong chart: the reviewer can take it with
 * them. This module builds those artifacts from two recorded inputs only —
 * the dashboard bundle, and a pytest collection inventory — and refuses to
 * fill a gap with a constant.
 */

import type { Bundle, Metric, MetricRow } from "./types";

export interface CollectedTest {
  /** Pytest node id relative to `solution/`, e.g. `tests/test_cleaning.py::...`. */
  nodeid: string;
  /** First line of the test's docstring, when it has one. Not a result. */
  doc: string | null;
}

export interface TestInventory {
  /** The command that produced `tests`. Collection, not execution. */
  command: string;
  /** Working directory of that command, relative to the repository root. */
  cwd: string;
  collected: number;
  tests: CollectedTest[];
}

export interface InventoryModule {
  file: string;
  tests: CollectedTest[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Accept the inventory file, or nothing.
 *
 * A file that claims a pass/fail status is rejected outright. Collection
 * does not know whether a test passed, and a UI that renders a status from
 * this file would be repeating the bug this replaced.
 */
export function parseTestInventory(value: unknown): TestInventory | null {
  if (!isRecord(value)) return null;
  if (typeof value.command !== "string" || typeof value.cwd !== "string") return null;
  if (typeof value.collected !== "number" || !Number.isInteger(value.collected)) return null;
  if (!Array.isArray(value.tests)) return null;

  const tests: CollectedTest[] = [];
  for (const entry of value.tests) {
    if (!isRecord(entry)) return null;
    if ("status" in entry || "passed" in entry) return null;
    if (typeof entry.nodeid !== "string" || !entry.nodeid.includes("::")) return null;
    if (entry.doc !== null && typeof entry.doc !== "string") return null;
    tests.push({ nodeid: entry.nodeid, doc: entry.doc });
  }
  if (tests.length !== value.collected) return null;
  return { command: value.command, cwd: value.cwd, collected: value.collected, tests };
}

/** Group node ids by their file, preserving collection order. */
export function modulesOf(inventory: TestInventory): InventoryModule[] {
  const modules: InventoryModule[] = [];
  const byFile = new Map<string, InventoryModule>();
  for (const test of inventory.tests) {
    const file = test.nodeid.split("::")[0] ?? test.nodeid;
    let module = byFile.get(file);
    if (!module) {
      module = { file, tests: [] };
      byFile.set(file, module);
      modules.push(module);
    }
    module.tests.push(test);
  }
  return modules;
}

function columnsOf(rows: MetricRow[]): string[] {
  const seen: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!seen.includes(key)) seen.push(key);
    }
  }
  return seen;
}

function csvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = String(value);
  if (/[",\n\r]/.test(text)) return `"${text.replace(/"/g, '""')}"`;
  return text;
}

/** CSV of one metric's recorded rows. Null when that metric has no rows. */
export function metricToCsv(metric: Pick<Metric, "rows"> | undefined): string | null {
  const rows = metric?.rows;
  if (!rows?.length) return null;
  const columns = columnsOf(rows);
  const lines = [columns.join(",")];
  for (const row of rows) lines.push(columns.map((column) => csvCell(row[column])).join(","));
  return lines.join("\n");
}

function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function line(label: string, value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  return `- **${label}:** ${value}`;
}

/**
 * A short briefing whose every figure is a field on the bundle.
 *
 * Lines whose field is absent are omitted. Nothing here is a remembered
 * dollar amount, a store name, or a pass count.
 */
export function briefingMarkdown(bundle: Bundle): string {
  const recon = bundle.analytics?.metrics?.revenue_reconciliation?.rows?.[0];
  const counts = bundle.run?.row_counts;
  const warehouse = counts?.warehouse;
  const lines = [
    "# Run briefing",
    "",
    "Generated from the dashboard bundle loaded for this build. A figure appears only when that field is present. This file does not report test results; those come from running the suite.",
    "",
    "## Run",
    line("As of", bundle.run?.as_of_date),
    line("Generated at", bundle.run?.generated_at),
    line("Pipeline version", bundle.run?.pipeline_version),
    line("Status", bundle.run?.status),
    line("Coverage mismatches", bundle.run?.mismatch_count),
    "",
    "## Row counts",
    line("Raw transactions", counts?.raw?.transactions),
    line("Cleaned transactions", counts?.cleaned?.transactions ?? counts?.clean?.transactions),
    line("Quarantined rows (pipeline count)", counts?.quarantined),
    line("fact_sales", warehouse?.fact_sales),
    line("Foreign-key violations", warehouse?.fk_violations),
    line("Revenue tie-out (cents)", warehouse?.revenue_tie_out_cents),
    "",
    "## Revenue reconciliation",
    line("Gross list value", finite(recon?.gross_list_value)),
    line("Discount total", finite(recon?.discount_total)),
    line("Returns value", finite(recon?.returns_value)),
    line("Net revenue", finite(recon?.net_revenue)),
    line("Aggregate delta", finite(recon?.aggregate_delta)),
    "",
    "Re-run `python scripts/verify_submission.py` from the repository root to regenerate the pass/fail table. Do not treat this briefing as that table.",
    "",
  ];
  return lines.filter((entry): entry is string => entry !== null).join("\n");
}
