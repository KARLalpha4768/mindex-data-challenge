/**
 * Post-response numeric self-audit.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * `SYSTEM_INSTRUCTION` forbids the model from stating any figure that is not
 * literally present in the supplied context. Nothing verified that. The rule
 * was a request, and a request to a language model is not a control.
 *
 * For a submission whose entire argument is numerical trustworthiness, that gap
 * is the expensive one: a single invented dollar figure in front of a reviewer
 * retroactively discredits every other number in the artefact, including the
 * ones that are right. So every answer — model-generated or scripted — is run
 * through this module before it is rendered, and the panel says what came back.
 *
 * WHAT THIS PROVES, AND WHAT IT DOES NOT
 * --------------------------------------
 * It proves one thing: each figure in the answer APPEARS in the material the
 * answer was grounded on (or is simple arithmetic over figures that do). It
 * does NOT prove the figure was used correctly. "Net revenue is $9,952.03" is
 * false, and this verifier passes it, because $9,952.03 is in the context — it
 * is the returns value. A verifier that claimed more than this would itself be
 * the kind of over-claim the project is about catching, so the UI copy, the
 * `limitation` string on every result, and this comment all say so plainly.
 *
 * THE DERIVED-VS-INVENTED RULE (the design decision that matters)
 * --------------------------------------------------------------
 * The naive verifier — "flag every number not found in context" — is useless.
 * It fires on "the three stores", on years, on `TX-03`, on line numbers, and on
 * every percentage the model was explicitly asked to compute. A reviewer who
 * sees the badge cry wolf once stops reading it, at which point the badge is
 * worse than no badge, because it has spent the credibility it was built to
 * earn. So a figure is exempted or forgiven under exactly four rules:
 *
 *   1. IT IS NOT A CLAIM ABOUT THE DATA. Defect codes (`TX-03`), entity ids
 *      (`S006`, `P005`, `CUST0213`), dates and years, and `file.py:214` code
 *      citations are masked out before checking. These are pointers, not
 *      magnitudes: a reviewer verifies them by clicking through to the Defect
 *      Explorer or the Code Viewer, and reformatting ("June 2026" for
 *      "2026-06") defeats literal matching anyway. Counted, reported by kind,
 *      never flagged.
 *
 *   2. IT IS A SMALL CARDINAL USED AS PROSE. A bare integer ≤ 12 with no
 *      currency symbol and no percent sign is not checked. Twelve is where
 *      English stops spelling numbers as enumeration ("the three stores", "two
 *      of them", "one day of data") and starts using them as measurements, and
 *      small integers collide with everything — ranks, stages, list positions.
 *      This is a deliberate, documented blind spot: a wrong "three" is
 *      recoverable and usually contradicted by the answer's own visible list; a
 *      wrong "$158,044.29" is not. The cost asymmetry decides it. Note that
 *      `$5` and `5%` are still checked — a unit means it is a measurement.
 *
 *   3. IT APPEARS IN CONTEXT. Compared on ABSOLUTE VALUE, at the coarser of the
 *      two literals' precisions, with thousands separators and currency symbols
 *      normalised away, so `$158,044.29`, `158044.29` and `158,044.3` are the
 *      same figure. Sign is deliberately ignored: returns are negative by
 *      construction in the bundle and a model writing "$9,952.03 of returns"
 *      has restated, not invented.
 *
 *   4. IT IS DERIVED FROM FIGURES THE ANSWER ITSELF SHOWS. The system
 *      instruction already requires that if the model does arithmetic it must
 *      show both operands. That requirement is what makes this checkable: the
 *      derivation pool is NOT every number in the context, it is only the
 *      figures in THIS answer that already passed rule 3, plus the constant
 *      100 for percentage conversion. A figure is "derived" if it equals
 *      a+b, a−b, a×b, a÷b, 100·a÷b, a÷100 or a×100 for such operands.
 *
 *      Using the answer's own verified figures rather than the whole context is
 *      the entire point. The context holds hundreds of numbers; the pairwise
 *      sums and differences of hundreds of numbers cover so much of the number
 *      line that a hallucination would land on one by coincidence and be
 *      excused. An answer holds a handful, and they are visible to the reviewer
 *      on screen, so "derived" is a claim the reviewer can check by eye.
 *
 * Derived figures are reported as their own category rather than folded into
 * "verified", because they are a weaker guarantee and the difference is the
 * reviewer's to weigh.
 *
 * PURE AND DETERMINISTIC. No network, no clock, no `process.env`, no `node:fs`.
 * The client imports it to audit scripted answers; the server imports it to
 * audit model answers. Same code, same rules, so the badge means the same thing
 * in both modes.
 */

import type {
  FigureCheck,
  FigureVerdict,
  NumericAudit,
  NumericAuditSource,
} from "./chatContract";
import type { Bundle } from "./types";

/* ────────────────────────────────────────────────────────────────────────── *
 * 1. Masking — the spans that are not claims about the data
 *
 * These run BEFORE number extraction. Anything a mask covers is exempt under
 * rule 1 and is reported by kind rather than checked.
 * ────────────────────────────────────────────────────────────────────────── */

/** Exemption kinds, in the order they are applied. First match names the span. */
export type ExemptKind = "date" | "code-reference" | "identifier" | "small-integer";

interface Span {
  start: number;
  end: number;
  kind: ExemptKind;
}

/**
 * Ordered mask patterns. Order is priority: a span is labelled by the first
 * pattern that produced it, and later patterns cannot re-label it.
 *
 * `date` first because `2026-06-02` would otherwise be shredded into `2026`,
 * `-06` and `-02` by the number scanner. `code-reference` before `identifier`
 * so `src/cleaning/transactions.py:214` keeps its line number inside the span
 * instead of leaving `214` loose.
 */
const MASKS: Array<[ExemptKind, RegExp]> = [
  // ISO timestamps, ISO dates, ISO year-months, US and EU date literals.
  ["date", /\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?\b/g],
  ["date", /\b\d{4}-\d{2}\b/g],
  ["date", /\b\d{1,2}\/\d{1,2}\/\d{4}\b/g],
  ["date", /\b\d{1,2}-\d{1,2}-\d{4}\b/g],
  // Bare years. 1900-2199 is wide enough for a pipeline artefact and narrow
  // enough that a count or an amount will not be mistaken for one.
  ["date", /(?<![\d.,$])\b(?:19|20|21)\d{2}\b(?![\d.,%])/g],

  // `src/cleaning/transactions.py:214`, `queries.py:12-40`, `line 214`.
  ["code-reference", /\b[\w./\\-]+\.(?:py|ts|tsx|js|jsx|sql|json|csv|md|db|yml|yaml)(?::\d+(?:\s*[-–]\s*\d+)?)+/g],
  ["code-reference", /\blines?\s+\d+(?:\s*[-–]\s*\d+)?\b/gi],

  // Identifiers that carry digits: TX-03, ST-01, S006, P005, CUST0213,
  // gemini-3.6-flash, v1.0.0, warehouse.db.
  ["identifier", /\b[A-Za-z][A-Za-z_]*-?\d[\w.-]*\b/g],
  // Trailing-unit identifiers: 30d, 2xx, 17th, 5th.
  ["identifier", /(?<![\d.,])\b\d+(?:st|nd|rd|th|[A-Za-z]{1,3})\b/g],
];

/** Build the merged, non-overlapping mask set for a piece of answer text. */
function maskSpans(text: string): Span[] {
  const spans: Span[] = [];
  for (const [kind, pattern] of MASKS) {
    pattern.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = pattern.exec(text)) !== null) {
      const start = m.index;
      const end = start + m[0].length;
      // First pattern to claim a region owns it; later ones may not overlap.
      if (spans.some((s) => start < s.end && end > s.start)) continue;
      spans.push({ start, end, kind });
    }
  }
  return spans.sort((a, b) => a.start - b.start);
}

function coveringSpan(spans: Span[], start: number, end: number): Span | null {
  for (const s of spans) {
    if (start < s.end && end > s.start) return s;
  }
  return null;
}

/* ────────────────────────────────────────────────────────────────────────── *
 * 2. Numeric literals — extraction and normalisation
 * ────────────────────────────────────────────────────────────────────────── */

/**
 * One numeric literal: `$158,044.29`, `-9,952.03`, `13.73%`, `474`.
 *
 * The leading sign is captured so it can be discarded deliberately rather than
 * by accident (see rule 3 — sign is a presentation choice here, not a claim).
 */
const LITERAL_RE = /[-+]?\$?\s?\d[\d,]*(?:\.\d+)?\s?%?/g;

/** A parsed literal, reduced to the two facts that matter for comparison. */
interface ParsedLiteral {
  /** Absolute magnitude. `$158,044.29` -> 158044.29. */
  value: number;
  /** Decimal places written. Drives the rounding tolerance. */
  decimals: number;
  /** True when the literal carried `%`; adds the /100 reading. */
  isPercent: boolean;
  /** True when the literal carried a currency symbol. Blocks the small-int exemption. */
  isCurrency: boolean;
}

function parseLiteral(raw: string): ParsedLiteral | null {
  const isPercent = raw.includes("%");
  const isCurrency = raw.includes("$");
  const digits = raw.replace(/[^0-9.]/g, "");
  if (digits === "" || digits === ".") return null;
  const value = Number(digits);
  if (!Number.isFinite(value)) return null;
  const dot = digits.indexOf(".");
  const decimals = dot === -1 ? 0 : digits.length - dot - 1;
  return { value, decimals, isPercent, isCurrency };
}

/**
 * Compare two figures at the COARSER of their two precisions.
 *
 * `158044.29` (2 dp) against `158,044.3` (1 dp) compares at 1 dp and matches,
 * which is the rounding tolerance the brief asks for. `159005.77` against
 * `158044.29` compares at 2 dp and does not, which is the case that matters.
 * Scaling to integers before rounding avoids the usual binary-float surprises
 * (`158044.29 * 100` is `15804428.999999998` before `Math.round`).
 */
function sameFigure(a: number, aDecimals: number, b: number, bDecimals: number): boolean {
  const p = Math.min(aDecimals, bDecimals, 6);
  const f = 10 ** p;
  return Math.round(a * f) === Math.round(b * f);
}

/* ────────────────────────────────────────────────────────────────────────── *
 * 3. The context index
 *
 * A sorted list of every magnitude the answer was allowed to see. Sorted so
 * lookup is a binary search plus a short local scan rather than a full pass per
 * figure — an answer with thirty figures against a whole-bundle index would
 * otherwise be a few million string comparisons.
 * ────────────────────────────────────────────────────────────────────────── */

export interface NumberIndex {
  /** Ascending by `value`. */
  entries: Array<{ value: number; decimals: number }>;
  /** What the index was built from. Drives the UI copy; never inferred later. */
  source: NumericAuditSource;
}

/**
 * Index every numeric literal in a block of text.
 *
 * Deliberately permissive: numbers inside identifiers, dates and file paths are
 * indexed too. Being generous on the CONTEXT side can only cause a figure to be
 * accepted, and the failure mode this module exists to prevent is a false
 * accusation, not a missed one. The strictness lives on the answer side.
 */
export function indexNumbers(text: string, source: NumericAuditSource): NumberIndex {
  const seen = new Set<string>();
  const entries: Array<{ value: number; decimals: number }> = [];

  LITERAL_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = LITERAL_RE.exec(text)) !== null) {
    const parsed = parseLiteral(m[0]);
    if (!parsed) continue;
    const key = `${parsed.value}|${parsed.decimals}`;
    if (seen.has(key)) continue;
    seen.add(key);
    entries.push({ value: parsed.value, decimals: parsed.decimals });
  }

  entries.sort((a, b) => a.value - b.value);
  return { entries, source };
}

/**
 * Index the whole bundle, for auditing the scripted (offline) answers.
 *
 * Scripted answers are assembled from `bundle.json` in its entirety rather than
 * from a retrieved slice, so "appears in the bundle" is the honest provenance
 * claim for them and the badge says exactly that. It is a weaker claim than the
 * live path's — a whole-bundle index holds tens of thousands of magnitudes and
 * therefore forgives more — which is why `source` travels with the result and
 * the UI never renders one as though it were the other.
 */
export function indexBundleNumbers(bundle: Bundle): NumberIndex {
  return indexNumbers(JSON.stringify(bundle), "bundle");
}

/** Does any indexed magnitude match `value` at the coarser shared precision? */
function inIndex(index: NumberIndex, value: number, decimals: number): boolean {
  const { entries } = index;
  if (entries.length === 0) return false;

  // Widest possible tolerance is ±0.5 (both literals integral), so a window of
  // 0.5 either side of the target cannot miss a match.
  const lo = value - 0.5;
  const hi = value + 0.5;

  let left = 0;
  let right = entries.length;
  while (left < right) {
    const mid = (left + right) >> 1;
    if (entries[mid].value < lo) left = mid + 1;
    else right = mid;
  }
  for (let i = left; i < entries.length && entries[i].value <= hi; i += 1) {
    if (sameFigure(value, decimals, entries[i].value, entries[i].decimals)) return true;
  }
  return false;
}

/* ────────────────────────────────────────────────────────────────────────── *
 * 4. Derivation — rule 4
 * ────────────────────────────────────────────────────────────────────────── */

/**
 * How many of the answer's own verified figures may serve as operands.
 *
 * Bounded because the search is O(n²) over the pool and this runs inside a
 * request handler. Twenty-four is far above what any real answer carries, and
 * the pool is filled in reading order, so the cap can only ever bite on an
 * answer that is already an exhaustive table.
 */
const MAX_DERIVATION_OPERANDS = 24;

interface Derivation {
  ok: boolean;
  /** Human-readable, e.g. "= 158044.29 + 961.48". Rendered in the UI. */
  note: string;
}

/**
 * Try to explain `value` as arithmetic over figures the answer itself shows.
 *
 * The operand pool is the answer's already-verified figures plus the constant
 * 100 (percentages and cents are the only unit conversions the bundle needs).
 * See the header comment for why the pool is not the whole context.
 */
function deriveFrom(value: number, decimals: number, pool: number[]): Derivation {
  const operands = [...pool.slice(0, MAX_DERIVATION_OPERANDS), 100];

  const test = (candidate: number, note: string): Derivation | null => {
    if (!Number.isFinite(candidate)) return null;
    return sameFigure(value, decimals, Math.abs(candidate), decimals)
      ? { ok: true, note }
      : null;
  };

  for (let i = 0; i < operands.length; i += 1) {
    const a = operands[i];

    // Unary: unit conversion only. Cents to dollars and back is the one the
    // bundle actually needs (`revenue_tie_out_cents`).
    const unary =
      test(a / 100, `= ${fmt(a)} ÷ 100`) ?? test(a * 100, `= ${fmt(a)} × 100`);
    if (unary) return unary;

    for (let j = 0; j < operands.length; j += 1) {
      if (i === j) continue;
      const b = operands[j];
      const hit =
        test(a + b, `= ${fmt(a)} + ${fmt(b)}`) ??
        test(a - b, `= ${fmt(a)} − ${fmt(b)}`) ??
        test(a * b, `= ${fmt(a)} × ${fmt(b)}`) ??
        (b !== 0 ? test(a / b, `= ${fmt(a)} ÷ ${fmt(b)}`) : null) ??
        (b !== 0 ? test((a / b) * 100, `= 100 × ${fmt(a)} ÷ ${fmt(b)}`) : null);
      if (hit) return hit;
    }
  }
  return { ok: false, note: "" };
}

function fmt(n: number): string {
  return Number.isInteger(n) ? String(n) : String(Number(n.toFixed(6)));
}

/* ────────────────────────────────────────────────────────────────────────── *
 * 5. The audit itself
 * ────────────────────────────────────────────────────────────────────────── */

/**
 * Bare integers at or below this are treated as prose, not measurement.
 * See rule 2 in the header for why, and why the blind spot is stated out loud
 * rather than hidden.
 */
export const SMALL_INTEGER_CEILING = 12;

/** Payload bound. An answer listing 200 figures does not need 200 in the JSON. */
const MAX_REPORTED_FIGURES = 40;

/** Characters of surrounding answer text shown with a flagged figure. */
const EXCERPT_RADIUS = 44;

const LIMITATION =
  "This check proves each figure appears in the material the answer was grounded on " +
  "(or is arithmetic over figures that do). It does not prove the figure was used " +
  "correctly, attributed to the right entity, or that the reasoning around it is sound.";

function excerptAround(text: string, start: number, end: number): string {
  const from = Math.max(0, start - EXCERPT_RADIUS);
  const to = Math.min(text.length, end + EXCERPT_RADIUS);
  const body = text.slice(from, to).replace(/\s+/g, " ").trim();
  return `${from > 0 ? "…" : ""}${body}${to < text.length ? "…" : ""}`;
}

/**
 * Audit one answer against one index.
 *
 * Two passes, and the order is load-bearing: every figure is checked against
 * the index first, so that the derivation pool used in the second pass contains
 * only figures that are themselves grounded. A derivation built on an
 * ungrounded operand would launder the invention it was supposed to catch.
 */
export function auditAnswer(answer: string, index: NumberIndex): NumericAudit {
  const spans = maskSpans(answer);

  const exemptByKind: Record<string, number> = {};
  const noteExempt = (kind: ExemptKind) => {
    exemptByKind[kind] = (exemptByKind[kind] ?? 0) + 1;
  };

  interface Candidate {
    raw: string;
    start: number;
    end: number;
    parsed: ParsedLiteral;
  }
  const candidates: Candidate[] = [];

  LITERAL_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = LITERAL_RE.exec(answer)) !== null) {
    const raw = m[0];
    const start = m.index;
    const end = start + raw.length;

    const masked = coveringSpan(spans, start, end);
    if (masked) {
      noteExempt(masked.kind);
      continue;
    }

    const parsed = parseLiteral(raw);
    if (!parsed) continue;

    // Rule 2. A unit ($ or %) means measurement, so the exemption does not apply.
    if (
      !parsed.isCurrency &&
      !parsed.isPercent &&
      parsed.decimals === 0 &&
      parsed.value <= SMALL_INTEGER_CEILING
    ) {
      noteExempt("small-integer");
      continue;
    }

    candidates.push({ raw: raw.trim(), start, end, parsed });
  }

  // Pass 1 — rule 3.
  const figures: FigureCheck[] = [];
  const verifiedPool: number[] = [];

  for (const c of candidates) {
    const { value, decimals, isPercent } = c.parsed;

    // A percent literal has two honest readings: the bundle may carry the
    // metric already scaled (`unit_return_rate_pct = 13.73`) or as a ratio
    // (`return_rate_alert_threshold = 0.1`). Both are the same claim.
    const readings: Array<[number, number]> = isPercent
      ? [
          [value, decimals],
          [value / 100, decimals + 2],
        ]
      : [[value, decimals]];

    const hit = readings.find(([v, d]) => inIndex(index, v, d));
    if (hit) {
      verifiedPool.push(value);
      figures.push({
        text: c.raw,
        value,
        verdict: "verified",
        note:
          hit[0] === value
            ? "present in context"
            : "present in context as a ratio (÷100)",
        excerpt: excerptAround(answer, c.start, c.end),
      });
    } else {
      figures.push({
        text: c.raw,
        value,
        verdict: "unverified",
        note: "not found",
        excerpt: excerptAround(answer, c.start, c.end),
      });
    }
  }

  // Pass 2 — rule 4. Only figures still unverified, only against operands the
  // answer showed and pass 1 already grounded.
  for (const figure of figures) {
    if (figure.verdict !== "unverified") continue;
    const source = candidates.find((c) => c.raw === figure.text && c.parsed.value === figure.value);
    const decimals = source?.parsed.decimals ?? 0;
    const derivation = deriveFrom(figure.value, decimals, verifiedPool);
    if (derivation.ok) {
      figure.verdict = "derived";
      figure.note = `computed from figures in this answer (${derivation.note})`;
    }
  }

  const count = (v: FigureVerdict) => figures.filter((f) => f.verdict === v).length;
  const verified = count("verified");
  const derived = count("derived");
  const unverified = count("unverified");

  // Unverified figures are listed first: if the payload is truncated, the ones
  // a reviewer needs to see are the ones that survive.
  const ordered = [
    ...figures.filter((f) => f.verdict === "unverified"),
    ...figures.filter((f) => f.verdict === "derived"),
    ...figures.filter((f) => f.verdict === "verified"),
  ].slice(0, MAX_REPORTED_FIGURES);

  return {
    verdict: figures.length === 0 ? "no-figures" : unverified > 0 ? "warn" : "pass",
    source: index.source,
    checked: figures.length,
    verified,
    derived,
    unverified,
    exemptCount: Object.values(exemptByKind).reduce((a, b) => a + b, 0),
    exemptByKind,
    figures: ordered,
    truncated: ordered.length < figures.length,
    limitation: LIMITATION,
  };
}

/** Convenience for the server path: audit against the retrieved context text. */
export function auditAgainstContext(answer: string, contextText: string): NumericAudit {
  return auditAnswer(answer, indexNumbers(contextText, "retrieved-context"));
}
