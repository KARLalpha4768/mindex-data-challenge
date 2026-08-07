/**
 * Type contract for `output/dashboard_bundle.json`.
 *
 * This file is the single source of truth for the pipeline <-> dashboard
 * interface. If the Python side changes a key, TypeScript should break here
 * first and everywhere else second. Keep it structural and permissive at the
 * leaves (analytics rows are open records) and strict at the spine (run,
 * defect_catalog, audit), because the spine is what the UI reasons about.
 */

/** Severity vocabulary, ordered most severe first. See SEVERITY_ORDER. */
export type Severity = "critical" | "high" | "medium" | "low";

/** The action a cleaning pass took. Mirrors CONTRACT.md §4 DefectRecord.action. */
export type AuditAction =
  | "dropped"
  | "imputed"
  | "flagged"
  | "quarantined"
  | "preserved";

export type DatasetName = "stores" | "products" | "transactions";

export interface WarehouseCounts {
  dim_date: number;
  dim_store: number;
  dim_product: number;
  dim_customer: number;
  fact_sales: number;
  fk_violations: number;
  revenue_tie_out_cents: number;
}

export interface RunMeta {
  /** ISO-8601 timestamp of the pipeline run that produced this bundle. */
  generated_at: string;
  /** The frozen analysis date (2026-06-02). NOT wall-clock now(). */
  as_of_date: string;
  python_version?: string;
  pipeline_version?: string;
  catalog_version?: string;
  duration_seconds?: number;
  status?: string;
  mismatch_count?: number;
  row_counts: {
    raw: Record<string, number>;
    /** The pipeline writes "cleaned", not "clean". Accept both. */
    clean?: Record<string, number>;
    cleaned?: Record<string, number>;
    quarantined?: number;
    warehouse?: WarehouseCounts;
  };
}

/** One of the 17 seeded defect classes, as specified (not as observed). */
export interface DefectSpec {
  code: string;
  dataset: string;
  title: string;
  severity: Severity;
  /** From seed_data.py. `null` when the count is data-dependent. */
  expected_count: number | null;
  detection: string;
  decision: string;
  rationale: string;
  /** e.g. "src/cleaning/transactions.py:reconcile_totals". */
  source_ref: string;
}

/** What the pipeline actually found and did, per defect class. */
export interface AuditEntry {
  code: string;
  detected_count: number;
  action: AuditAction;
  /** Business keys, capped at 50 by the serialiser. */
  affected_keys: string[];
  notes: string;
}

export interface ColumnProfile {
  name: string;
  dtype: string;
  null_count: number;
  null_pct: number;
  distinct_count: number;
  min: string | number | null;
  max: string | number | null;
  sample_values: string[];
}

export interface DatasetProfile {
  row_count: number;
  columns: ColumnProfile[];
  duplicate_row_count: number;
}

/**
 * The bundle's profiling block has an envelope:
 *   { generated_at, as_of_date, datasets: { stores: {...}, products: {...}, transactions: {...} } }
 */
export interface ProfilingEnvelope {
  generated_at?: string;
  as_of_date?: string;
  datasets: Record<string, DatasetProfile>;
}

/** Analytics rows are heterogeneous by metric; the UI derives columns from keys. */
export type MetricRow = Record<string, string | number | boolean | null>;

export interface Metric {
  title?: string;
  description?: string;
  /** e.g. "src/analytics/queries.py:RETURN_RATE_BY_STORE". */
  sql_ref?: string;
  /** Explicit numerator/denominator. Rendered prominently — never collapsed. */
  definition_note?: string;
  /** The literal SQL executed. */
  sql?: string;
  /**
   * Per-column unit declared by the SQL author in
   * `src/analytics/queries.py:METRIC_REGISTRY`.
   *
   * This is the ONLY sanctioned source of scale information. The UI must never
   * infer whether a number is a 0-1 ratio or a 0-100 percentage from its
   * magnitude — that guess renders a correct 12.5 as "1250.00%" and a correct
   * 1.5x growth as "1.50%". Absent for bundles generated before units existed,
   * in which case the formatter falls back to a documented naming convention.
   */
  column_units?: Record<string, ColumnUnit>;
  rows: MetricRow[];
}

/** Mirrors the vocabulary in `src/analytics/queries.py`. */
export type ColumnUnit =
  | "percent"
  | "ratio"
  | "currency"
  | "integer"
  | "flag"
  | "text";

/** One `# DEFECT: <CODE>` tag site found in the pipeline source. */
export interface CodeRef {
  /** Repo-relative, e.g. "src/cleaning/transactions.py". */
  path: string;
  /** 1-based line number of the tagged line. */
  line: number;
  snippet: string;
}

export interface SourceFile {
  /** Full file contents, split on newlines, 0-indexed (line N is lines[N-1]). */
  lines: string[];
  language: string;
}

export interface Bundle {
  run: RunMeta;
  defect_catalog: DefectSpec[];
  audit: AuditEntry[];
  /**
   * Profiling may be either the envelope form { datasets: {...} }
   * or a direct Record<string, DatasetProfile> (legacy mock).
   */
  profiling: ProfilingEnvelope | Record<string, DatasetProfile>;
  analytics: { metrics: Record<string, Metric> };
  code_index: Record<string, CodeRef[]>;
  source_files: Record<string, SourceFile>;
  coverage?: {
    expected_classes: number;
    detected_classes: number;
    matched_classes: number;
    untagged_codes: string[];
    mismatches: string[];
  };
}

/**
 * Helper: resolve the actual datasets map from the profiling block,
 * regardless of whether it's the envelope or direct form.
 */
export function resolveProfilingDatasets(
  profiling: Bundle["profiling"],
): Record<string, DatasetProfile> {
  if (!profiling) return {};
  // Envelope form: has a `datasets` key that is an object of DatasetProfiles
  if ("datasets" in profiling && profiling.datasets && typeof profiling.datasets === "object") {
    const ds = profiling.datasets as Record<string, unknown>;
    // Verify it actually contains dataset profiles (has row_count)
    const firstVal = Object.values(ds)[0];
    if (firstVal && typeof firstVal === "object" && "row_count" in (firstVal as Record<string, unknown>)) {
      return ds as Record<string, DatasetProfile>;
    }
  }
  // Direct form: the profiling object itself is Record<string, DatasetProfile>
  // Filter out non-dataset keys (generated_at, as_of_date are strings, not objects)
  const result: Record<string, DatasetProfile> = {};
  for (const [key, val] of Object.entries(profiling)) {
    if (val && typeof val === "object" && "row_count" in val) {
      result[key] = val as DatasetProfile;
    }
  }
  return result;
}

/**
 * Helper: resolve cleaned row counts. The bundle uses "cleaned" but
 * the mock/legacy may use "clean". Accept both.
 */
export function resolveCleanedCounts(
  rowCounts: RunMeta["row_counts"],
): Record<string, number> {
  return rowCounts.cleaned ?? rowCounts.clean ?? {};
}

/**
 * A catalog entry joined to its audit entry, plus the derived coverage verdict.
 * This is the row shape the Defect Explorer works with.
 */
export interface DefectView extends DefectSpec {
  audit: AuditEntry | null;
  detected_count: number | null;
  /**
   * "match"   — detected === expected (or expected is null and we found some)
   * "mismatch" — detected !== expected
   * "missing"  — the pipeline produced no audit entry for this class at all
   */
  coverage: "match" | "mismatch" | "missing";
  refs: CodeRef[];
}

/* ────────────────────────────────────────────────────────────────────────── *
 * `public/data/csv_diff.json` — the raw-versus-clean cell diff
 *
 * A SECOND artefact, written by the same pipeline run as `bundle.json` and kept
 * separate from it because it is row-level rather than run-level: one entry per
 * source row per dataset, carrying both values of every cell and the defect code
 * that explains any difference between them. `bundle.json` is what the pipeline
 * CONCLUDED; this file is what it SAW, cell by cell.
 *
 * The shapes live here, in the shared type module, rather than being redeclared
 * in each consumer, because there are now three of them and they must not drift:
 *
 *   • `RawVsCleanInspector.tsx` renders it in the browser (fetched at runtime);
 *   • `csvDiff.ts` reads it from disk on the server for `/api/chat`;
 *   • `grounding.ts` turns one row of it into a prompt block.
 *
 * The last two are the reason it is typed at all: when a reviewer clicks a cell,
 * the browser sends only COORDINATES and the server resolves the CONTENT from
 * this file. Both ends therefore have to agree about the shape, and a private
 * copy of the interface in each of them is exactly how they would stop agreeing.
 * ────────────────────────────────────────────────────────────────────────── */

/**
 * One cell, as the diff writer emits it.
 *
 * `status` is the cell's role in the diff, not a severity:
 *   "clean" — raw and clean agree; nothing happened here.
 *   "error" — the raw value was defective and the row/value did not survive
 *             intact (dropped, quarantined, or left flagged).
 *   "fixed" — the pipeline changed the value, or deliberately preserved it and
 *             exposed the discrepancy elsewhere (TX-03 is of this kind).
 * `explanation` is prose the pipeline itself wrote for this cell; it is quoted
 * verbatim into the prompt and never paraphrased.
 */
export interface CsvDiffCell {
  raw_value: string;
  clean_value: string;
  /**
   * What the pipeline did with this cell.
   *
   * `preserved` exists because "flagged" and "wrong" are not the same thing. A
   * return's negative quantity (TX-10) and a silent discount's reported total
   * (TX-03) are both correct data the pipeline deliberately declined to touch —
   * and both are among the most important findings in the submission. Rendering
   * them as errors told a reviewer the exact opposite of the decision on screen.
   */
  status: "clean" | "error" | "fixed" | "preserved";
  defect_code: string | null;
  explanation: string | null;
}

/**
 * One source row.
 *
 * `row_id` is the natural key (transaction/product/store id) and is NOT unique:
 * the 15 TX-09 rows are exact duplicates and share one transaction id by
 * definition — that is the defect the inspector exists to display. The stable,
 * unique identifier for a row is its POSITION in `CsvDiffDataset.rows`, which is
 * what the client sends as `rowIndex` and what the server looks up.
 */
export interface CsvDiffRow {
  row_id: string;
  /** Defect codes present anywhere in this row, as the diff writer recorded them. */
  defects: string[];
  /** Keyed by column name. Every header should be present; readers tolerate gaps. */
  cells: Record<string, CsvDiffCell>;
}

export interface CsvDiffDataset {
  /** Column order as it appears in the source CSV. Also the allow-list for `column`. */
  headers: string[];
  rows: CsvDiffRow[];
}

/** The whole file. Every dataset optional: a partial file must still render. */
export type CsvDiff = Partial<Record<DatasetName, CsvDiffDataset>>;

/** The three dataset names this artefact can carry, as a runtime list. */
export const CSV_DIFF_DATASETS: readonly DatasetName[] = ["stores", "products", "transactions"];
