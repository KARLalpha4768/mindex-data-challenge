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
  /**
   * Gemini returned 401 or 403: the key itself was rejected.
   *
   * WHY THIS IS ITS OWN KIND AND NOT JUST ANOTHER `upstream_error`.
   * Every other upstream failure is something the server can work around — a
   * 404 means try the next model, a 429 means wait and retry, a 5xx means the
   * service is having a bad minute. A 401/403 is the one class where no amount
   * of code changes the outcome: the key is invalid, revoked, restricted to
   * other referrers/APIs, or its Google Cloud project has no access to the
   * Generative Language API. Collapsing it into `upstream_error` is what makes
   * an operator spend an evening rewriting retry logic against a problem that
   * lives in the API console. `ChatFailure.remedy` carries the fix.
   *
   * Additive: a client built before this kind existed falls through its
   * `ERROR_COPY[kind] ?? default` lookup and still renders a sane message.
   */
  | "upstream_auth"
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

/* ── Model resolution (added after v2; every field below is additive) ──────
 *
 * WHY THIS IS ON THE WIRE AT ALL.
 * The single most expensive failure this deployment had was a live, correctly
 * configured assistant that returned "the model API call failed" for every
 * question, with no way to tell from the outside whether the key was wrong, the
 * model name was wrong, or Google was down. Those three have three different
 * fixes and the client could distinguish none of them.
 *
 * So the server now says what it tried. `ModelResolution` is a diagnosis
 * surface: which names were in the queue, which ones the API key's own project
 * actually reports, which were skipped and why, and which one produced the text
 * on screen. It carries no secret — model names are public strings.
 *
 * Every field is optional or on an optional object: a client built against the
 * earlier contract ignores the lot and renders exactly as it did before.
 */

/** What happened on one candidate model. */
export type ModelAttemptOutcome =
  /** This model produced the answer. */
  | "answered"
  /**
   * Model-specific rejection (404, or a 400 naming the model). The model is
   * retired for the life of the instance and the chain moved on. NOT an error
   * the user needs to act on — it is the fallback working.
   */
  | "skipped"
  /** Tried and failed for a reason that is not the model's fault (429, 5xx, network). */
  | "failed";

export interface ModelAttempt {
  model: string;
  outcome: ModelAttemptOutcome;
  /** Upstream HTTP status, when there was one. Absent for a thrown fetch. */
  status?: number;
  /** How many times this model was called, including retries. */
  attempts: number;
  /** Short, safe, human phrase. Never an upstream body, never a stack trace. */
  reason: string;
}

/** How the candidate list was arrived at, and what happened to it. */
export interface ModelResolution {
  /** `GEMINI_MODEL` env override, when one is set. It jumps the queue. */
  requested?: string;
  /** The static preference order, before discovery filtered it. */
  preference: string[];
  /** The order actually tried on this request, after discovery and retirement. */
  candidates: string[];
  /** The model that answered. Absent when nothing answered. */
  selected?: string;
  /**
   * `listed`  — ListModels answered and the candidate list is filtered by it.
   * `unavailable` — ListModels failed/was empty; the preference list was tried blind.
   * `not-attempted` — no live question has been asked on this instance yet.
   */
  discovery: "listed" | "unavailable" | "not-attempted";
  /** One line saying what discovery did. Rendered verbatim. */
  discoveryNote: string;
  /** Per-candidate log, in the order they were tried. */
  attempts: ModelAttempt[];
  /** Candidates dropped mid-chain for a model-specific reason. */
  skipped: string[];
  /** Preference entries that ListModels did not report for this key's project. */
  unavailable: string[];
}

export interface ChatSuccess {
  ok: true;
  answer: string;
  /**
   * The model that actually produced this text. This is the RESOLVED name, not
   * the configured preference — if the first choice 404'd for this key and the
   * second answered, this is the second. The UI shows it verbatim so a reviewer
   * is never told an answer came from a model that did not produce it.
   */
  model: string;
  context: ChatContextSummary;
  /** Token usage as reported by the upstream, when it reports any. */
  usage?: { promptTokens?: number; responseTokens?: number; totalTokens?: number };
  /**
   * Result of the post-response numeric check. Optional for backward
   * compatibility: an older client ignores it and renders the answer as before.
   */
  audit?: NumericAudit;
  /**
   * What the model-selection chain did to produce this answer. Optional and
   * ignored by older clients.
   */
  resolution?: ModelResolution;
}

export interface ChatFailure {
  ok: false;
  kind: ChatErrorKind;
  /** Safe for display. Never contains a key, a stack trace or an upstream body. */
  message: string;
  retryAfterSeconds?: number;
  /**
   * The concrete fix, when the failure has one the operator can act on.
   * Populated for `upstream_auth` (regenerate the key / check its restrictions)
   * and left absent everywhere the server cannot honestly name a remedy.
   * Optional: an older client simply does not render it.
   */
  remedy?: string;
  /**
   * The candidate chain as it stood when the call failed. Present only for
   * failures that got as far as calling a model, so the client can say "three
   * models were tried" rather than "it failed".
   */
  resolution?: ModelResolution;
}

export type ChatResponse = ChatSuccess | ChatFailure;

/** GET /api/chat — cheap capability probe so the UI can label itself honestly on open. */
export interface ChatStatusResponse {
  /** True iff GEMINI_API_KEY is present server-side. The key itself is never sent. */
  configured: boolean;
  /**
   * The model the next question would go to: the resolved one once something
   * has answered on this instance, otherwise the head of the preference list.
   * Unchanged in meaning and position from v1, so old clients keep working.
   */
  model: string;
  /** False when the bundle could not be read; the route would fail even with a key. */
  bundleAvailable: boolean;

  /* ── Additive diagnosis fields ─────────────────────────────────────────
   * GET is a cheap probe and deliberately stays cheap: it reports the
   * instance's CURRENT resolution state and never spends a network call to
   * improve it. Discovery runs on the first live question, not on the probe,
   * because a reviewer who opens the panel and closes it again should not cost
   * the deployment an upstream round-trip.
   */

  /** The model that has actually answered on this instance. `null` until one has. */
  resolvedModel?: string | null;
  /** Static preference order, override first. */
  preference?: string[];
  /** The order the next question would try, after discovery and retirement. */
  candidates?: string[];
  /** The `GEMINI_MODEL` env override, when set. */
  modelOverride?: string;
  /** State of ListModels discovery on this instance. */
  discovery?: "listed" | "unavailable" | "not-attempted";
  /** One line explaining that state. */
  discoveryNote?: string;
  /** Models retired this instance after a model-specific rejection, with the reason. */
  retired?: Array<{ model: string; reason: string }>;
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
