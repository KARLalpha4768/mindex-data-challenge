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

  return [runSummaryAnswer(facts), ...defects, ...metrics];
}

/**
 * Offline retrieval: pick the best scripted answer for a free-text question.
 *
 * Same philosophy as the server-side selector, at a smaller scale — an exact
 * defect code wins outright, otherwise term overlap decides, and the run
 * summary is the floor. It never returns nothing, because "no match" and a
 * silent panel are the same experience to a reviewer.
 */
export function findScriptedAnswer(
  answers: ScriptedAnswer[],
  question: string,
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

  let best: ScriptedAnswer | null = null;
  let bestScore = 0;
  for (const answer of answers) {
    const haystack = `${answer.label} ${answer.question} ${answer.keywords.join(" ")}`.toLowerCase();
    let score = 0;
    for (const term of terms) if (haystack.includes(term)) score += 1;
    if (score > bestScore) {
      bestScore = score;
      best = answer;
    }
  }

  // Index 0 is always the run summary; it is the honest "here is what I do
  // have" response rather than a guess at what was asked.
  return bestScore > 0 && best ? best : answers[0];
}
