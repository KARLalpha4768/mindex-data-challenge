/**
 * The wire contract between `ChatAssistant.tsx` (browser) and
 * `/api/chat` (server).
 *
 * Kept in its own module, importable from both sides, for one reason: the
 * client has to be able to tell "the assistant is not configured on this
 * deployment" apart from "the assistant is configured and the call failed".
 * Those two states get different UI copy — the first is an expected state of a
 * public demo whose owner may not want to fund an API key, the second is a
 * fault. Collapsing them into "something went wrong" would lie about both.
 *
 * Nothing here is secret. This file is bundled into the browser build; it must
 * never grow a key, a URL with a key in it, or anything else server-only.
 */

/** Discriminated failure taxonomy. The client switches on this, not on strings. */
export type ChatErrorKind =
  /** GEMINI_API_KEY is unset on this deployment. Expected; fall back quietly. */
  | "not_configured"
  /** Per-IP rate limit tripped. `retryAfterSeconds` is set. */
  | "rate_limited"
  /** Malformed or over-long request. Client bug or abuse. */
  | "bad_request"
  /** Body exceeded the size cap before it was even parsed. */
  | "too_large"
  /** The data bundle could not be read server-side, so nothing could be grounded. */
  | "bundle_unavailable"
  /** Gemini returned a non-2xx, or the response could not be parsed. */
  | "upstream_error"
  /** Gemini refused on safety grounds, or the finish reason was not STOP. */
  | "blocked"
  /** Upstream took longer than the server's timeout. */
  | "timeout"
  /** 2xx from Gemini but no usable text in the candidate. */
  | "empty_response";

/** One prior turn, as replayed by the client. */
export interface ChatTurn {
  role: "user" | "model";
  text: string;
}

export interface ChatRequestBody {
  question: string;
  /** Oldest first. Server truncates to the most recent `MAX_HISTORY_TURNS`. */
  history?: ChatTurn[];
}

/**
 * What the server decided to put in front of the model. Surfaced in the UI so a
 * reviewer can see the assistant is retrieving rather than free-associating —
 * and can tell when the budget forced something out.
 */
export interface ChatContextSummary {
  approxTokens: number;
  budgetTokens: number;
  includedIds: string[];
  droppedIds: string[];
  mentionedCodes: string[];
  /**
   * Alias phrases (from `grounding.ts`'s hand-authored table) that fired for
   * this question and pulled a defect or metric into the context. Optional so
   * a client built against the earlier contract is unaffected.
   */
  aliasPhrases?: string[];
}

/* ── Numeric self-audit (added after v1; every field below is additive) ────
 *
 * The server verifies the model's own arithmetic honesty before replying: every
 * numeric literal in the answer is checked against the context that was
 * retrieved for it. `ChatSuccess.audit` is OPTIONAL precisely so that a client
 * built against the earlier contract keeps working — it will ignore a field it
 * does not know about, and the answer text is unchanged either way.
 *
 * The implementation and the exemption rules live in `numericAudit.ts`; only
 * the wire shape is declared here.
 */

/** Per-figure outcome. `derived` is a weaker guarantee than `verified`, on purpose. */
export type FigureVerdict =
  /** The figure appears in the grounding material. */
  | "verified"
  /** Not present, but equals simple arithmetic over figures this answer showed. */
  | "derived"
  /** Neither. This is the state the badge warns about. */
  | "unverified";

export interface FigureCheck {
  /** The literal as written in the answer, e.g. "$158,044.29". */
  text: string;
  /** Its absolute magnitude. Sign is not part of the comparison. */
  value: number;
  verdict: FigureVerdict;
  /** Short human explanation, e.g. "= 158044.29 + 961.48". */
  note: string;
  /** Surrounding answer text, so a reviewer can see the claim, not just the number. */
  excerpt: string;
}

/** What the answer was checked against. Drives the UI copy; never inferred. */
export type NumericAuditSource =
  /** The slice of the bundle the server retrieved for this question. */
  | "retrieved-context"
  /** The whole of bundle.json — used for the scripted offline answers. */
  | "bundle";

export interface NumericAudit {
  /** `no-figures` when the answer states no checkable figure at all. */
  verdict: "pass" | "warn" | "no-figures";
  source: NumericAuditSource;
  /** Figures actually checked (exempt ones are excluded from this count). */
  checked: number;
  verified: number;
  derived: number;
  unverified: number;
  /** Literals skipped as non-claims: dates, ids, code refs, small cardinals. */
  exemptCount: number;
  exemptByKind: Record<string, number>;
  /** Unverified first, then derived, then verified. Capped for payload size. */
  figures: FigureCheck[];
  /** True when `figures` was capped and does not list every checked figure. */
  truncated: boolean;
  /** What the verdict does NOT prove. Rendered verbatim in the UI. */
  limitation: string;
}

export interface ChatSuccess {
  ok: true;
  answer: string;
  model: string;
  context: ChatContextSummary;
  /** Token usage as reported by the upstream, when it reports any. */
  usage?: { promptTokens?: number; responseTokens?: number; totalTokens?: number };
  /**
   * Result of the post-response numeric check. Optional for backward
   * compatibility: an older client ignores it and renders the answer as before.
   */
  audit?: NumericAudit;
}

export interface ChatFailure {
  ok: false;
  kind: ChatErrorKind;
  /** Safe for display. Never contains a key, a stack trace or an upstream body. */
  message: string;
  retryAfterSeconds?: number;
}

export type ChatResponse = ChatSuccess | ChatFailure;

/** GET /api/chat — cheap capability probe so the UI can label itself honestly on open. */
export interface ChatStatusResponse {
  /** True iff GEMINI_API_KEY is present server-side. The key itself is never sent. */
  configured: boolean;
  model: string;
  /** False when the bundle could not be read; the route would fail even with a key. */
  bundleAvailable: boolean;
}

/* ── Limits. Shared so the client can pre-trim instead of eating a 400. ──── */

/** Longest question accepted, in characters. */
export const MAX_QUESTION_CHARS = 1200;
/** Longest single replayed turn, in characters. */
export const MAX_TURN_CHARS = 4000;
/** How many prior turns are replayed. Older turns are dropped, oldest first. */
export const MAX_HISTORY_TURNS = 6;
/** Hard cap on the raw request body, in bytes. */
export const MAX_BODY_BYTES = 24 * 1024;
