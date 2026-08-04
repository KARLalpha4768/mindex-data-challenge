/**
 * Pipeline lineage model.
 *
 * Hardcoded for the same reason as schema.ts: this is the ARCHITECTURE, not a
 * pipeline output. What the bundle supplies is the evidence hung off each stage
 * (defect counts, code links); the stage graph itself is a design statement.
 *
 * Each stage declares the defect codes it OWNS. Ownership is exclusive: every
 * one of the 17 codes appears against exactly one stage, so the lineage view
 * doubles as a proof that nothing is unhandled. `assertLineageCoversAll` in the
 * Lineage component checks this at render time against the catalog.
 */

export interface LineageStage {
  id: string;
  label: string;
  /** The artefact this stage reads. */
  input: string;
  /** The artefact this stage writes. */
  output: string;
  /** One sentence: what this stage is FOR. */
  summary: string;
  /** Defect codes handled here. Exclusive across stages. */
  codes: string[];
  /** Primary module implementing the stage. */
  module: string;
}

export const LINEAGE_STAGES: LineageStage[] = [
  {
    id: "ingest",
    label: "Ingest",
    input: "data/raw/*.csv",
    output: "raw DataFrames (all columns dtype=object)",
    summary:
      "Everything is read as text. No dtype inference on read — inference is what turns '$142.50' into NaN and '0938' into 938 before anyone has had a chance to notice.",
    codes: [],
    module: "src/io_utils.py",
  },
  {
    id: "profile",
    label: "Profile",
    input: "raw DataFrames",
    output: "output/profiling_report.json",
    summary:
      "Column-level census taken BEFORE any cleaning: null counts, distinct counts, ranges, duplicate rows. This is the baseline every later count is measured against.",
    codes: [],
    module: "src/profiling/profiler.py",
  },
  {
    id: "clean-stores",
    label: "Clean · stores",
    input: "raw stores (16 rows)",
    output: "clean stores (15 rows)",
    summary:
      "ZIP normalisation with an unverifiable flag, documented survivorship for the duplicated primary key, region imputed only from values the column already contains.",
    codes: ["ST-01", "ST-02", "ST-03"],
    module: "src/cleaning/stores.py",
  },
  {
    id: "clean-products",
    label: "Clean · products",
    input: "raw products (32 rows)",
    output: "clean products (30 rows)",
    summary:
      "Exact duplicates dropped FIRST, so that any product_id collision still standing is by construction a real value conflict rather than an extract artefact.",
    codes: ["PR-01", "PR-02", "PR-03", "PR-04"],
    module: "src/cleaning/products.py",
  },
  {
    id: "clean-transactions",
    label: "Clean · transactions",
    input: "raw transactions (505 rows)",
    output: "clean transactions (474 rows) + 4 quarantine files",
    summary:
      "Parse, then dedupe, then flag returns, then reconcile totals, then enforce referential integrity. The order is load-bearing: reconciling before flagging returns would read every return as a 100% discount.",
    codes: [
      "TX-01",
      "TX-02",
      "TX-03",
      "TX-04",
      "TX-05",
      "TX-06",
      "TX-07",
      "TX-08",
      "TX-09",
      "TX-10",
    ],
    module: "src/cleaning/transactions.py",
  },
  {
    id: "warehouse",
    label: "Star schema load",
    input: "clean frames",
    output: "output/warehouse.db",
    summary:
      "Dimensions then fact, in one transaction with foreign keys enforced. Rolls back whole on any failure, so a half-loaded warehouse cannot exist.",
    codes: [],
    module: "src/warehouse/loader.py",
  },
  {
    id: "analytics",
    label: "Analytics",
    input: "output/warehouse.db",
    output: "output/analytics.json",
    summary:
      "Six named SQL constants, each carrying its own numerator/denominator statement. Every rate forces float division and guards its denominator with NULLIF.",
    codes: [],
    module: "src/analytics/runner.py",
  },
  {
    id: "audit",
    label: "Audit & bundle",
    input: "AuditLog + all artefacts",
    output: "output/dashboard_bundle.json",
    summary:
      "Cross-checks detected counts against the seeded expectations and fails the run on any mismatch, then greps the source for '# DEFECT:' tags to build the code index this dashboard links through.",
    codes: [],
    module: "src/audit.py",
  },
];

/** Stage id -> stage, for the Defect Explorer's back-reference. */
export const STAGE_BY_CODE: Record<string, LineageStage> = Object.fromEntries(
  LINEAGE_STAGES.flatMap((stage) => stage.codes.map((code) => [code, stage])),
);
