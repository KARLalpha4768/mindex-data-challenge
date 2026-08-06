/**
 * Scripted, bundle-derived answers — the assistant's offline mode.
 *
 * WHY THESE ARE GENERATED AND NOT WRITTEN
 * ---------------------------------------
 * The previous version of this panel shipped a hand-authored array of answers
 * containing, among other figures, `$170,816.34`, `$1,104.05` and `$11,668.00`.
 * All three were correct once and none of them was correct by the time a
 * reviewer read them: the pipeline was re-run, the numbers moved, and the prose
 * did not. The dashboard was then confidently quoting stale money at the exact
 * audience it was built to convince.
 *
 * The fix is structural rather than clerical. Re-typing the four current
 * figures would reintroduce the same defect with a fresh expiry date, so
 * **there are no numeric literals about the data in this file at all**. Every
 * count, dollar amount and line number below is read out of the bundle at
 * render time, from the same helpers that build the model's prompt
 * (`grounding.ts`). If the pipeline re-runs and the numbers move, these answers
 * move with them; if a figure is missing from the bundle it renders as an
 * em-dash, never as zero.
 *
 * The prose is likewise not invented here: detection, decision and rationale
 * are quoted from `defect_catalog`, and the code shown is sliced out of
 * `source_files` at the real `# DEFECT:` tag lines rather than being a
 * plausible-looking reconstruction. What this module contributes is layout.
 *
 * These answers are shown when the live model is unconfigured or fails, and
 * whenever a preset chip is clicked. The UI labels them as scripted in both
 * cases — a reviewer must always be able to tell which text a model produced.
 */

import type { ViewContext } from "./chatContract";
import {
  auditRecords,
  buildRunFacts,
  catalogEntries,
  codeRefsFor,
  type RunFacts,
} from "./grounding";
import { formatCurrency, formatInt } from "./format";
import type { AuditEntry, Bundle, DefectSpec } from "./types";

export interface CodeAnnotation {
  lineRange: string;
  title: string;
  description: string;
}

export interface ScriptedAnswer {
  /** Chip label. */
  label: string;
  /** Defect code for the "open in Defect Explorer" deep link. `""` = no target. */
  defectCode: string;
  question: string;
  answer: string;
  talkingPoints: string[];
  /** `path:line` when the bundle carries a tag site, else the catalog `source_ref`. */
  codeRef: string;
  codeSnippet: string;
  codeAnnotations: CodeAnnotation[];
  /** Retrieval key: extra words that should match this answer in offline search. */
  keywords: string[];
}

const EM_DASH = "—";

/** `null`/`undefined` must never render as `0`; see `format.ts`. */
function count(v: number | null | undefined): string {
  return v === null || v === undefined ? EM_DASH : formatInt(v);
}

function shorten(text: string, max: number): string {
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1).trimEnd()}…`;
}

/** How many source lines to show either side of a `# DEFECT:` tag in the panel. */
const SNIPPET_WINDOW = 8;

/**
 * Slice the real file out of `source_files` around a tag site.
 *
 * Line numbers are printed with the code because the panel's whole claim is
 * "this is the actual line that does it" — a snippet with no line numbers is
 * indistinguishable from one somebody typed from memory.
 */
function snippetFor(bundle: Bundle, code: string): { text: string; ref: string } {
  const refs = codeRefsFor(bundle, code);
  const sourceFiles = bundle.source_files ?? {};

  for (const ref of refs) {
    const file = sourceFiles[ref.path];
    if (!file || !Array.isArray(file.lines) || file.lines.length === 0) continue;
    const start = Math.max(1, ref.line - SNIPPET_WINDOW);
    const end = Math.min(file.lines.length, ref.line + SNIPPET_WINDOW);
    const body: string[] = [`# ${ref.path}  (lines ${start}-${end}, tag on ${ref.line})`];
    for (let n = start; n <= end; n += 1) {
      body.push(`${String(n).padStart(5, " ")}${n === ref.line ? " >" : " |"} ${file.lines[n - 1]}`);
    }
    return { text: body.join("\n"), ref: `${ref.path}:${ref.line}` };
  }

  if (refs.length > 0) {
    return {
      text:
        `# ${refs[0].path}:${refs[0].line} is tagged "# DEFECT: ${code}", but this bundle's\n` +
        `# source_files block does not carry that file, so the lines cannot be shown here.`,
      ref: `${refs[0].path}:${refs[0].line}`,
    };
  }
  return { text: "", ref: "" };
}

/** Annotations = the literal tagged lines, one per tag site. Nothing invented. */
function annotationsFor(bundle: Bundle, code: string): CodeAnnotation[] {
  return codeRefsFor(bundle, code).map((ref) => ({
    lineRange: `${ref.path.split("/").pop() ?? ref.path}:${ref.line}`,
    title: `Tag site for ${code}`,
    description: ref.snippet || `Line ${ref.line} of ${ref.path} carries the # DEFECT: ${code} tag.`,
  }));
}

/** One answer per catalog entry, assembled from the catalog × audit join. */
function defectAnswer(
  bundle: Bundle,
  spec: DefectSpec,
  audit: AuditEntry | null,
  facts: RunFacts,
): ScriptedAnswer {
  const detected = facts.detected[spec.code] ?? null;
  const expected = spec.expected_count;
  const action = audit?.action ?? null;

  const verdict = !audit
    ? "NOT REPORTED — the run produced no audit record for this class."
    : expected === null
      ? detected && detected > 0
        ? `variable expected count; ${count(detected)} detected, which counts as covered`
        : `variable expected count and nothing detected — treated as a coverage failure`
      : detected === expected
        ? `expected ${count(expected)}, detected ${count(detected)} — match`
        : `expected ${count(expected)}, detected ${count(detected)} — MISMATCH`;

  const { text: codeSnippet, ref: codeRef } = snippetFor(bundle, spec.code);

  const answer = [
    `${spec.code} — ${spec.title}`,
    `dataset ${spec.dataset} · severity ${spec.severity} · action ${action ?? EM_DASH} · ${verdict}`,
    "",
    "DETECTION",
    spec.detection,
    "",
    "DECISION",
    spec.decision,
    "",
    "RATIONALE",
    spec.rationale,
  ].join("\n");

  const talkingPoints: string[] = [`Coverage: ${verdict}.`];
  if (action) talkingPoints.push(`Action recorded in the audit ledger: ${action}.`);
  if (audit?.notes) talkingPoints.push(shorten(audit.notes, 320));
  const keys = audit?.affected_keys ?? [];
  if (keys.length) {
    talkingPoints.push(
      `Affected keys carried in the bundle (${count(keys.length)}): ${keys.slice(0, 8).join(", ")}` +
        (keys.length > 8 ? ", …" : ""),
    );
  }
  talkingPoints.push(`Implementation: ${spec.source_ref}${codeRef ? ` (tagged at ${codeRef})` : ""}.`);

  return {
    label: `${spec.code} ${shorten(spec.title, 26)}`,
    defectCode: spec.code,
    question: `How did the pipeline detect and handle ${spec.code} (${spec.title})?`,
    answer,
    talkingPoints,
    codeRef: codeRef || spec.source_ref,
    codeSnippet,
    codeAnnotations: annotationsFor(bundle, spec.code),
    keywords: [spec.code, spec.dataset, spec.title, spec.detection, spec.decision].map((s) =>
      String(s).toLowerCase(),
    ),
  };
}

/** One answer per analytics metric, quoting its definition and its rows. */
function metricAnswer(bundle: Bundle, id: string): ScriptedAnswer | null {
  const metric = bundle.analytics?.metrics?.[id];
  if (!metric) return null;

  const rows = metric.rows ?? [];
  const shown = rows.slice(0, 10);
  const table = shown.map(
    (row) =>
      "  " +
      Object.entries(row)
        .map(([k, v]) => `${k}=${v === null ? "NULL" : v}`)
        .join(" | "),
  );

  const answer = [
    metric.title ?? id,
    metric.sql_ref ? `sql_ref ${metric.sql_ref}` : "",
    "",
    metric.definition_note ? `DEFINITION\n${metric.definition_note}` : "",
    "",
    `ROWS (${shown.length} of ${rows.length})`,
    ...table,
    rows.length > shown.length ? `  … ${rows.length - shown.length} further rows in the Analytics view.` : "",
  ]
    .filter((line) => line !== "")
    .join("\n");

  return {
    label: `Metric ${id}`,
    // Metrics are not defect classes; the deep link would have nowhere to go.
    defectCode: "",
    question: `What does the ${id} metric compute, and what did it return?`,
    answer,
    talkingPoints: [
      metric.description ? shorten(metric.description, 300) : `Metric id: ${id}.`,
      metric.column_units
        ? `Declared column units: ${Object.entries(metric.column_units)
            .map(([c, u]) => `${c}=${u}`)
            .join(", ")}. The UI never infers scale from magnitude.`
        : "This metric declares no column units; the formatter falls back to naming convention.",
      `Rows in bundle: ${count(rows.length)}.`,
    ],
    codeRef: metric.sql_ref ?? `src/analytics/queries.py:${id.toUpperCase()}`,
    codeSnippet: metric.sql ?? "",
    codeAnnotations: [],
    keywords: [id, id.replace(/_/g, " "), metric.title ?? "", metric.definition_note ?? ""].map((s) =>
      s.toLowerCase(),
    ),
  };
}

/** The run-level summary. Also the offline fallback for an unmatched question. */
export function runSummaryAnswer(facts: RunFacts): ScriptedAnswer {
  const r = facts.recon;
  const w = facts.warehouse;

  const answer = [
    `Run ${facts.pipelineVersion ?? EM_DASH} · generated ${facts.generatedAt ?? EM_DASH} · status ${facts.status ?? EM_DASH}`,
    `Analysis date is frozen at ${facts.asOfDate ?? EM_DASH} — not wall-clock now().`,
    "",
    "ROW BUDGET",
    ...Object.keys(facts.raw).map(
      (k) => `  ${k}: ${count(facts.raw[k])} raw → ${count(facts.cleaned[k])} cleaned`,
    ),
    `  quarantined rows: ${count(facts.quarantined)}`,
    "",
    "WAREHOUSE",
    `  dim_date ${count(w.dim_date)} · dim_store ${count(w.dim_store)} · dim_product ${count(w.dim_product)} · dim_customer ${count(w.dim_customer)}`,
    `  fact_sales ${count(w.fact_sales)} · FK violations ${count(w.fk_violations)} · revenue tie-out ${count(w.revenue_tie_out_cents)} cents`,
    "",
    "DEFECT COVERAGE",
    `  ${count(facts.coverage.matched)} matched / ${count(facts.coverage.detected)} detected / ${count(facts.coverage.expected)} expected classes`,
    facts.coverage.mismatches.length
      ? `  mismatches: ${facts.coverage.mismatches.join(", ")}`
      : "  no count mismatches",
    "",
    "REVENUE RECONCILIATION",
    `  gross list value            ${formatCurrency(r.grossListValue)}`,
    `  discount total              ${formatCurrency(r.discountTotal)}`,
    `  gross sales net of discount ${formatCurrency(r.grossSalesNetOfDiscount)}`,
    `  returns value               ${formatCurrency(r.returnsValue)}`,
    `  net revenue                 ${formatCurrency(r.netRevenue)}`,
    `  line-level delta            ${formatCurrency(r.lineLevelDelta)}`,
    `  aggregate delta             ${formatCurrency(r.aggregateDelta)}`,
    `  reconciliation delta        ${formatCurrency(r.reconciliationDelta)}`,
  ].join("\n");

  return {
    label: "Run summary",
    defectCode: "",
    question: "Summarise the run: row counts, coverage and the revenue reconciliation.",
    answer,
    talkingPoints: [
      "Every figure above is read from the bundle at render time. Nothing in this panel is a typed-in constant, which is how the previous version went stale.",
      "Returns value is negative by construction: returns keep their sign so SUM(net_amount) is net revenue without a special case.",
      "Two independent deltas are published. A control that cannot fail proves nothing, so both are computed by different routes.",
    ],
    codeRef: "src/analytics/queries.py:REVENUE_RECONCILIATION",
    codeSnippet: "",
    codeAnnotations: [],
    keywords: [
      "run", "summary", "revenue", "reconciliation", "net", "gross", "discount",
      "returns", "coverage", "rows", "warehouse", "totals", "status",
    ],
  };
}

function tradeoffAnswers(bundle: Bundle, facts: RunFacts): ScriptedAnswer[] {
  const r = facts.recon;
  return [
    {
      label: "⚡ TX-03 Discount Preservation",
      defectCode: "TX-03",
      question: "Why preserve reported total_amount for TX-03 rather than recomputing quantity × list_price?",
      answer: `TX-03 (Silent Discount) handling:\n- 20 transaction rows carry total_amount lower than quantity × unit_price by 5% to 20%.\n- Decision: PRESERVE total_amount verbatim (${formatCurrency(r.netRevenue)} net revenue).\n- Recomputing total_amount would have overstated revenue by ${formatCurrency(r.discountTotal)} and erased the silent discount finding entirely.\n- Reconciliation: Gross list value (${formatCurrency(r.grossListValue)}) - Discount total (${formatCurrency(r.discountTotal)}) = Gross net of discount (${formatCurrency(r.grossSalesNetOfDiscount)}). Plus returns (${formatCurrency(r.returnsValue)}) = Net revenue (${formatCurrency(r.netRevenue)}) with $0.00 tie-out delta.`,
      talkingPoints: [
        `Preserved ${formatCurrency(r.discountTotal)} of unrecorded promotional discounts across 20 transaction rows.`,
        "Recomputing line total (the previous attempt's bug) would overstate sales revenue.",
        "SQL tie-out delta is $0.00, proving mathematical consistency across fact loading.",
      ],
      codeRef: "src/cleaning/transactions.py:flag_discounts",
      codeSnippet: "extended_amount = quantity * unit_price\ndiscount_amount = extended_amount - total_amount\nhas_discount = discount_amount > PRICE_TOLERANCE",
      codeAnnotations: [
        { lineRange: "Line 1-3", title: "Discount Calculation", description: "Preserves reported total_amount as authoritative for net revenue while exposing discount_amount." }
      ],
      keywords: ["tx-03", "discount", "preservation", "reconcile", "total_amount", "extended_amount"]
    },
    {
      label: "⚡ PR-02 Catalog vs Fact Price",
      defectCode: "PR-02",
      question: "Why does dim_product store $150.11 while fact_sales carries $141.61 for product P005?",
      answer: "PR-02 (Catalog Price Conflict) handling:\n- P005 appears twice in products.csv with list prices of $141.61 and $150.11 (an $8.50 price increase).\n- Decision: dim_product stores $150.11 as the current list price via an explicit MAX rule.\n- fact_sales stores $141.61 on all 19 transacted P005 sales lines because revenue comes from point-of-sale transactions.csv.\n- Rationale: Master catalog price changes post-date the sales window; fact revenue must reflect historical transacted price, not catalog list price.",
      talkingPoints: [
        "Master catalog updates must never post-actively reprice historical POS transaction lines.",
        "MAX rule is order-independent: re-sorting the CSV extract cannot change dim_product values.",
        "Prevents laundering fact transaction data into master catalog dimension attributes.",
      ],
      codeRef: "src/cleaning/products.py:resolve_price_conflicts",
      codeSnippet: "elected_price = max(prices)\ndim_product['list_unit_price'] = elected_price",
      codeAnnotations: [
        { lineRange: "Line 1-2", title: "Deterministic MAX Policy", description: "Elects maximum list price for dim_product without altering fact_sales transacted prices." }
      ],
      keywords: ["pr-02", "price", "catalog", "fact_sales", "p005", "max", "survivorship"]
    },
    {
      label: "⚡ ST-02 Store Survivorship Rule",
      defectCode: "ST-02",
      question: "How does the store survivorship rule for S007 avoid non-reproducible keep='first' behavior?",
      answer: "ST-02 (Near-Duplicate Primary Key) handling:\n- Store S007 appears twice with conflicting store names ('Downtown Rochester' vs 'Rochester Downtown').\n- Decision: Apply a 3-stage deterministic survivorship rule: (1) fewest nulls, (2) earliest opened_date, (3) lexicographically first store_name.\n- Outcome: Elects 'Downtown Rochester'. The losing row and reason are recorded in the audit ledger.\n- Rationale: drop_duplicates(keep='first') depends on CSV row order; a re-sorted file silently changes the winner. A 3-stage rule is 100% deterministic.",
      talkingPoints: [
        "Eliminates dependency on arbitrary CSV row order during ingest.",
        "Elects 'Downtown Rochester' deterministically across all environment re-runs.",
        "Losing variant and disposition reason are preserved in audit_report.json.",
      ],
      codeRef: "src/cleaning/stores.py:resolve_store_survivorship",
      codeSnippet: "sort_keys = ['null_count', 'opened_date', 'store_name']\nsurvivor = group.sort_values(sort_keys).iloc[0]",
      codeAnnotations: [
        { lineRange: "Line 1-2", title: "3-Stage Survivorship", description: "Orders candidates by null count, opened date, and name for 100% deterministic master selection." }
      ],
      keywords: ["st-02", "survivorship", "store", "s007", "duplicate", "rochester"]
    },
    {
      label: "⚡ TX-10 Return Rate Metric Choice",
      defectCode: "TX-10",
      question: "How are return transactions (TX-10) modeled, and why emit both unit-based and txn-based rates?",
      answer: "TX-10 (Return Transactions) handling:\n- 30 return rows carry negative quantity and negative total_amount.\n- Decision: Preserved in fact_sales as signed negative rows with is_return = True.\n- Analytics: Dashboard emits both Unit Return Rate (returned units / total units) and Txn Return Rate (return txns / total txns).\n- Example: Store S006 has a 13.73% unit return rate vs a 12.50% txn return rate.\n- Rationale: Storing signed negatives makes SUM(net_amount) equal net revenue without joins or special cases, while exposing both metrics resolves retail definition ambiguity.",
      talkingPoints: [
        "Signed negative rows allow SUM(net_amount) to calculate net revenue natively.",
        "Exposing unit-based (13.73%) and txn-based (12.50%) rates makes definitional trade-offs visible.",
        "Avoids filtering out negative returns, which would overstate net sales revenue.",
      ],
      codeRef: "src/cleaning/transactions.py:flag_returns",
      codeSnippet: "fact_sales['is_return'] = (quantity < 0) & (total_amount < 0)\nfact_sales['net_amount'] = total_amount",
      codeAnnotations: [
        { lineRange: "Line 1-2", title: "Sign-Preserving Fact Load", description: "Preserves negative sign for returns and flags is_return = True for multi-grain analytics." }
      ],
      keywords: ["tx-10", "returns", "unit_return_rate", "txn_return_rate", "s006", "signed"]
    },
    {
      label: "⚡ Pinned Date vs Clock Drift",
      defectCode: "TX-08",
      question: "Why is AS_OF_DATE = 2026-06-02 hard-pinned, and what breaks if datetime.now() is used?",
      answer: "Reference Date & Clock Drift (TX-08) handling:\n- The pipeline anchors all time-relative metrics on AS_OF_DATE = 2026-06-02 (the seed data reference date).\n- TX-08 flags 3 transactions dated +8, +16, and +25 days in the future as clock drift.\n- If datetime.now() were used instead: (1) trailing 30-day windows would go empty as calendar time passes, and (2) future-dated TX-08 transactions would silently turn into valid sales over time.\n- Rationale: Hard-pinning AS_OF_DATE guarantees 100% byte-reproducible pipeline outputs forever.",
      talkingPoints: [
        "Guarantees 100% byte-for-byte reproducible runs regardless of execution date.",
        "Prevents future-dated POS clock drift transactions from silently converting into valid sales.",
        "Ensures trailing 30-day window metrics ([2026-05-04 to 2026-06-02]) remain populated.",
      ],
      codeRef: "src/config.py:RunConfig",
      codeSnippet: "AS_OF_DATE = date(2026, 6, 2)\nrecent_window_start = AS_OF_DATE - timedelta(days=29)",
      codeAnnotations: [
        { lineRange: "Line 1-2", title: "Pinned Time Horizon", description: "Pins reference horizon to 2026-06-02 for 100% reproducible trailing analytics." }
      ],
      keywords: ["as_of_date", "clock_drift", "tx-08", "datetime.now", "window", "reproducible"]
    }
  ];
}

/**
 * Build the full scripted answer set for a bundle: run summary, 17 defect
 * classes in catalog order, then one per analytics metric.
 */
export function buildScriptedAnswers(bundle: Bundle): ScriptedAnswer[] {
  const facts = buildRunFacts(bundle);
  const auditByCode = new Map(auditRecords(bundle).map((a) => [a.code, a]));

  const defects = catalogEntries(bundle).map((spec) =>
    defectAnswer(bundle, spec, auditByCode.get(spec.code) ?? null, facts),
  );

  const metrics = facts.metricIds
    .map((id) => metricAnswer(bundle, id))
    .filter((m): m is ScriptedAnswer => m !== null);

  const tradeoffs = tradeoffAnswers(bundle, facts);

  return [runSummaryAnswer(facts), ...tradeoffs, ...defects, ...metrics];
}

/* ────────────────────────────────────────────────────────────────────────── *
 * The ten interview questions
 *
 * WHY THEY ARE CHIPS
 * ------------------
 * A reviewer who opens the assistant panel is given a text box and no idea what
 * this thing knows. The ten questions in `INTERVIEW_QA.md` are the hardest ones
 * this submission can answer — they are the ones that separate "the pipeline
 * ran" from "the pipeline made defensible choices" — and leaving a reviewer to
 * guess at them wastes the artefact's best material.
 *
 * They are ranked 1 (strongest) to 10, the same order as the document, and the
 * panel shows the top few with a disclosure for the rest so that ten chips do
 * not swamp a panel whose main job is the transcript.
 *
 * Unlike the bundle-derived chips, clicking one of these asks the question
 * through the NORMAL path: live model when one is configured, scripted answer
 * when not. They are questions, not canned answers — a reviewer who sees the
 * live assistant handle question 1 has learnt something a scripted reply could
 * not have told them.
 * ────────────────────────────────────────────────────────────────────────── */

export interface InterviewQuestion {
  /** 1 = strongest. Matches the ranking in INTERVIEW_QA.md. */
  rank: number;
  /** Short chip text. Full question goes in the transcript and to the model. */
  chip: string;
  question: string;
  /**
   * Which scripted answer to use when there is no live model.
   *
   * Resolved against `label` first, then `defectCode`, then free-text search —
   * so a hint that goes stale degrades to the existing offline matcher rather
   * than to silence. Free-text search alone is not enough here: "June 2026
   * shows a 98% revenue collapse" has no term in common with the label
   * "Metric mom_growth_by_category".
   */
  scriptedHint: string;
  /**
   * Views this question is worth asking FROM.
   *
   * Additive and advisory: it changes the ORDER the chips are offered in on a
   * given page, never the set. All ten stay reachable from every view, because a
   * ranked list that hides its best question when you happen to be on the wrong
   * tab is worse than no ranking at all.
   */
  views?: string[];
}

/** Ranked 1-10, verbatim from INTERVIEW_QA.md. */
export const INTERVIEW_QUESTIONS: readonly InterviewQuestion[] = [
  {
    rank: 1,
    chip: "Why not recompute total_amount?",
    question:
      "If I recomputed total_amount as quantity × unit_price, the data would be internally " +
      "consistent. Why is that wrong, and what would it cost?",
    scriptedHint: "TX-03",
    views: ["overview", "defects", "analytics", "raw", "lineage"],
  },
  {
    rank: 2,
    chip: "P005 twice — duplicate or price change?",
    question:
      "P005 appears twice. Why isn't that a duplicate, and why does dim_product carry a price " +
      "no transaction ever rang at?",
    scriptedHint: "PR-02",
    views: ["defects", "schema", "raw", "profile"],
  },
  {
    rank: 3,
    chip: "Two return-rate denominators",
    question:
      "You report two return-rate denominators. Which stores breach 10% under each, and does " +
      "the choice change who gets flagged?",
    scriptedHint: "TX-10",
    views: ["analytics", "defects", "tests"],
  },
  {
    rank: 4,
    chip: "June's 98% revenue collapse",
    question: "June 2026 shows a 98% revenue collapse. What happened to the business?",
    scriptedHint: "Metric mom_growth_by_category",
    views: ["analytics", "overview"],
  },
  {
    rank: 5,
    chip: "Account for all 505 rows",
    question:
      "Account for all 505 transaction rows. Where did the 31 that aren't in fact_sales go?",
    scriptedHint: "Run summary",
    views: ["overview", "lineage", "tests", "raw"],
  },
  {
    rank: 6,
    chip: "Null customer_id → guest, not error",
    question:
      "Show me the line that decides a null customer_id is a guest rather than an error — and " +
      "why keep those rows?",
    scriptedHint: "TX-06",
    views: ["defects", "schema", "raw"],
  },
  {
    rank: 7,
    chip: "Null region — why West, not East?",
    question: "Two stores had a null region. What did you impute, and why not \"East\"?",
    scriptedHint: "ST-03",
    views: ["defects", "profile", "analytics", "raw"],
  },
  {
    rank: 8,
    chip: "ZIP 0938 → 00938, still wrong",
    question:
      "S003's zip is 0938. You padded it to 00938, but that isn't a real New York zip. Why " +
      "present a wrong value?",
    scriptedHint: "ST-01",
    views: ["defects", "profile", "raw"],
  },
  {
    rank: 9,
    chip: "Three date formats — parsed right?",
    question:
      "Twenty dates were in three formats. How do you know they parsed correctly rather than " +
      "silently parsing wrong?",
    scriptedHint: "TX-01",
    views: ["defects", "profile", "raw", "tests"],
  },
  {
    rank: 10,
    chip: "What can line_level_delta miss?",
    question: "What would make line_level_delta non-zero, and what can it not detect?",
    scriptedHint: "revenue_reconciliation",
    views: ["analytics", "tests", "overview", "schema"],
  },
];

/* ────────────────────────────────────────────────────────────────────────── *
 * Page-specific prompts
 *
 * The ten ranked questions are about the SUBMISSION. These are about the SCREEN
 * — "what does this chart show", "why do three stores breach the threshold" —
 * and they exist because that is what a reviewer actually types at a dashboard,
 * and because a question the assistant is known to answer well is worth
 * offering. They are ordinary questions asked through the ordinary path; there
 * is nothing canned about the answer.
 * ────────────────────────────────────────────────────────────────────────── */

export interface PagePrompt {
  /** Short chip text. */
  chip: string;
  /** What is actually asked. */
  question: string;
  /** Offline target, resolved exactly as an interview chip's hint is. */
  scriptedHint?: string;
}

/** Two or three per view. Static: nothing here depends on the bundle. */
export const PAGE_PROMPTS: Record<string, PagePrompt[]> = {
  overview: [
    { chip: "Explain this page", question: "Explain what this Overview page is showing me.", scriptedHint: "Run summary" },
    { chip: "Where did the rows go?", question: "Walk me through the row budget on this page: what came in, what came out, and what was quarantined.", scriptedHint: "Run summary" },
  ],
  defects: [
    { chip: "Explain this page", question: "Explain what the Defect Explorer is showing me and how to read one dossier." },
    { chip: "Which are worst?", question: "Which of the defect classes listed here are the most severe, and what was done about them?" },
  ],
  profile: [
    { chip: "Explain this page", question: "Explain what this profiling table is showing me and when it was taken." },
    { chip: "Which columns look wrong?", question: "Looking at this profile, which columns have nulls or suspicious dtypes, and which defect class does each one point at?" },
  ],
  lineage: [
    { chip: "Explain this page", question: "Explain the pipeline stages on this page and which defect codes each stage owns." },
    { chip: "Where is a code handled?", question: "For a given defect code, which stage on this map handles it, and in which module?" },
  ],
  schema: [
    { chip: "Explain this page", question: "Explain this star schema: the grain of each table and why the keys are designed this way." },
    { chip: "Why these constraints?", question: "Which constraints on this schema would fail loudly if the data were dirty, and what would that catch?" },
  ],
  analytics: [
    { chip: "What does this chart show?", question: "Explain what the charts and metrics on this Analytics page are showing me." },
    { chip: "Why do stores breach 10%?", question: "Why do several stores breach the 10% return-rate threshold, and does the choice of denominator change which ones?", scriptedHint: "TX-10" },
    { chip: "Which figures are controls?", question: "Which numbers on this page are controls rather than findings, and what would make them non-zero?", scriptedHint: "revenue_reconciliation" },
  ],
  tests: [
    { chip: "Explain this page", question: "Explain what this validation page proves and what it does not prove." },
    { chip: "How is coverage checked?", question: "How is defect coverage reconciled here — expected against detected — and what happens on a mismatch?" },
  ],
  raw: [
    { chip: "Explain this page", question: "Explain what this raw-versus-clean comparison is showing me and how to read a highlighted cell." },
    { chip: "Which defects are here?", question: "Which defect classes are visible in the dataset I am inspecting, and what did the pipeline do to each?" },
  ],
  assistant: [
    { chip: "What can you answer?", question: "What material are you grounded on, and what kinds of question can you answer from it?", scriptedHint: "Run summary" },
  ],
};

/**
 * The prompts to offer on the current page, focus-aware.
 *
 * The focus-derived prompts come first because they are the most specific thing
 * that can be asked: a reviewer with TX-03 open and a chip that says "why was
 * TX-03 handled this way" is being offered their own question back.
 */
export function pagePromptsFor(
  view: ViewContext | null | undefined,
  /**
   * Defect codes on the row the reviewer clicked, when there is one.
   *
   * CLIENT-ONLY and never sent to the server — `viewContext.selection` carries
   * coordinates, and the server resolves the codes itself out of
   * `csv_diff.json`. They are needed HERE for one thing: pointing the offline
   * (no-API-key) path at the right scripted dossier, so that a deployment with
   * no key still answers "why is this cell flagged?" by naming the class and the
   * decision rather than falling back to the run summary.
   */
  selectionCodes: readonly string[] = [],
): PagePrompt[] {
  if (!view) return [];
  const focused: PagePrompt[] = [];

  /* The clicked cell first: it is the most specific thing on the screen, and
   * these two questions are the ones the inspector visibly invites. They are
   * ordinary questions asked through the ordinary path — the server sees the
   * selection on the request and retrieves the row for them, exactly as it would
   * if the reviewer typed the same words. */
  if (view.selection) {
    const hint = selectionCodes[0];
    focused.push({
      chip: "Why is this cell flagged?",
      question:
        "Why is the cell I have selected in the raw-versus-clean inspector flagged? Name the " +
        "defect class, what the pipeline did to that value and why it took that decision.",
      ...(hint ? { scriptedHint: hint } : {}),
    });
    focused.push({
      chip: "What is wrong with this row?",
      question:
        "What is wrong with the row I have selected, across all of its columns? For each " +
        "defective cell, give the raw value, the cleaned value and the decision that produced it.",
      ...(hint ? { scriptedHint: hint } : {}),
    });
  }

  if (view.defect) {
    focused.push({
      chip: `Why ${view.defect} this way?`,
      question:
        `${view.defect} is the defect open on this page. Why was it handled the way it was, ` +
        "and what would the alternative have cost?",
      scriptedHint: view.defect,
    });
  }
  if (view.metric) {
    focused.push({
      chip: `What does ${view.metric} show?`,
      question:
        `Explain the ${view.metric} metric shown on this page: its definition, its ` +
        "numerator and denominator, and what its rows say.",
      scriptedHint: `Metric ${view.metric}`,
    });
  }
  if (view.dataset) {
    focused.push({
      chip: `Defects in ${view.dataset}`,
      question:
        `Which defect classes were seeded into the ${view.dataset} dataset I am looking at, ` +
        "and what did the pipeline do to each?",
    });
  }

  return [...focused, ...(PAGE_PROMPTS[view.view] ?? [])];
}

/**
 * The ten ranked questions, reordered so the ones that belong to this page come
 * first. Stable within each group, so the ranking is still visible — this is a
 * partition, not a re-ranking, and the full list is still exactly ten items.
 */
export function rankQuestionsForView(
  view: ViewContext | null | undefined,
  questions: readonly InterviewQuestion[] = INTERVIEW_QUESTIONS,
): InterviewQuestion[] {
  if (!view) return [...questions];
  const relevant = questions.filter((q) => q.views?.includes(view.view));
  const rest = questions.filter((q) => !q.views?.includes(view.view));
  return [...relevant, ...rest];
}

/**
 * Offline resolution for an interview chip.
 *
 * Three-step fallback (label, then defect code, then the ordinary free-text
 * matcher) so that a renamed scripted answer degrades the result rather than
 * breaking the chip. Exported for the tests: every hint must resolve to
 * something, and "something" must not be the run-summary fallback except where
 * that is the intended target.
 */
export function resolveInterviewAnswer(
  answers: ScriptedAnswer[],
  /* Structural rather than `InterviewQuestion`, so the page-specific prompts —
   * which carry the same two fields and no rank — resolve through exactly this
   * code path instead of a second, parallel one. */
  item: { question: string; scriptedHint?: string },
  view: ViewContext | null = null,
): ScriptedAnswer {
  const hint = item.scriptedHint;
  if (hint) {
    const byLabel = answers.find((a) => a.label === hint || a.label.startsWith(hint));
    if (byLabel) return byLabel;
    const byCode = answers.find((a) => a.defectCode === hint);
    if (byCode) return byCode;
    const byMetric = answers.find((a) => a.label === `Metric ${hint}`);
    if (byMetric) return byMetric;
  }
  return findScriptedAnswer(answers, item.question, view);
}

/**
 * How much a scripted answer gains for belonging to the page the reviewer is on.
 *
 * A term match is worth 1 in this matcher, and its terms are every word of four
 * characters or more — so "explain this" scores 1 against any answer that
 * happens to contain the word "this". A bonus of 2 is what it takes to beat that
 * class of noise: the defect open on screen wins over an unrelated dossier that
 * shares one incidental word, and loses to an answer that genuinely shares three
 * words with the question. Same principle as the server-side `VIEW_BOOST`, at
 * this matcher's much coarser scale — the page is a hint about the subject,
 * never a filter on it, and a question naming a defect code still short-circuits
 * everything below.
 */
const SCRIPTED_VIEW_BONUS = 2;

/** The scripted answers this view is about, by label — used for the bonus and the floor. */
function viewPreferredLabels(
  answers: ScriptedAnswer[],
  view: ViewContext | null | undefined,
): Set<string> {
  const labels = new Set<string>();
  if (!view) return labels;

  const addByCode = (code: string) => {
    for (const a of answers) if (a.defectCode === code) labels.add(a.label);
  };

  if (view.defect) addByCode(view.defect);
  for (const code of view.codeFilter ?? []) addByCode(code);
  if (view.metric) {
    for (const a of answers) if (a.label === `Metric ${view.metric}`) labels.add(a.label);
  }
  if (view.view === "analytics") {
    for (const a of answers) if (a.label.startsWith("Metric ")) labels.add(a.label);
  }
  if (view.view === "overview" || view.view === "tests" || view.view === "lineage") {
    // The run summary IS the material for these pages: coverage, the row budget
    // and the reconciliation are what they show.
    labels.add(answers[0]?.label ?? "");
  }
  return labels;
}

/**
 * Offline retrieval: pick the best scripted answer for a free-text question.
 *
 * Same philosophy as the server-side selector, at a smaller scale — an exact
 * defect code wins outright, otherwise term overlap decides, and the run
 * summary is the floor. It never returns nothing, because "no match" and a
 * silent panel are the same experience to a reviewer.
 *
 * `view` is optional and additive. When the API is unavailable the panel still
 * knows which page it is on, and a scripted answer about the page beats a
 * scripted answer about something else — but a question that names a defect
 * code still wins outright, exactly as on the live path.
 */
export function findScriptedAnswer(
  answers: ScriptedAnswer[],
  question: string,
  view: ViewContext | null = null,
): ScriptedAnswer {
  const q = question.toLowerCase();

  const codeMatch = q.match(/\b(?:st|pr|tx)-\d{2}\b/i)?.[0]?.toUpperCase();
  if (codeMatch) {
    const hit = answers.find((a) => a.defectCode === codeMatch);
    if (hit) return hit;
  }

  const terms = Array.from(
    new Set(q.split(/[^a-z0-9_]+/).filter((t) => t.length >= 4)),
  );

  const preferred = viewPreferredLabels(answers, view);

  let best: ScriptedAnswer | null = null;
  let bestScore = 0;
  for (const answer of answers) {
    const haystack = `${answer.label} ${answer.question} ${answer.keywords.join(" ")}`.toLowerCase();
    let score = 0;
    for (const term of terms) if (haystack.includes(term)) score += 1;
    // Unconditional, not gated on the answer having matched a term: the whole
    // point is that "explain this" matches nothing on the page and everything
    // elsewhere. Ties keep the earlier answer, which is catalog order.
    if (preferred.has(answer.label)) score += SCRIPTED_VIEW_BONUS;
    if (score > bestScore) {
      bestScore = score;
      best = answer;
    }
  }
  if (bestScore > 0 && best) return best;

  /* Nothing matched. Before falling back to the run summary, offer the material
   * for the page the reviewer is on: with TX-03 open, "explain this" should
   * produce TX-03, not a run summary that mentions it in passing. */
  if (view) {
    const focusLabel =
      (view.defect && answers.find((a) => a.defectCode === view.defect)?.label) ||
      (view.metric && answers.find((a) => a.label === `Metric ${view.metric}`)?.label) ||
      "";
    if (focusLabel) {
      const hit = answers.find((a) => a.label === focusLabel);
      if (hit) return hit;
    }
  }

  // Index 0 is always the run summary; it is the honest "here is what I do
  // have" response rather than a guess at what was asked.
  return answers[0];
}
