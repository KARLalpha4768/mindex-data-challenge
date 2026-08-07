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

/* ── View context (added after v4; every field below is additive) ─────────
 *
 * WHY THE PAGE THE REVIEWER IS ON IS PART OF THE WIRE CONTRACT.
 *
 * The dashboard is one route with nine views, and a reviewer asking "what does
 * this chart show?" or "why do three stores breach the threshold?" is asking
 * about the pixels in front of them. Retrieval that reads only the sentence
 * cannot know that: "what does this show" shares no vocabulary with any dossier,
 * so the selector falls back to the run-facts preamble and the model correctly
 * answers "the context does not contain that" — which reads as an assistant that
 * does not know its own dashboard.
 *
 * WHY IT IS SENT STRUCTURALLY RATHER THAN SCRAPED.
 * The alternative — the panel reading `window.location.hash`, or worse walking
 * the DOM for the active tab — was rejected on three grounds:
 *   1. `Dashboard.tsx` already OWNS this state. Re-deriving it from the URL in a
 *      second place means two parsers that can disagree, and the one in the chat
 *      panel would be the one nobody notices has drifted.
 *   2. The hash is a serialisation, not the state. `#defects/codes:TX-01,TX-02`
 *      has to be parsed to be useful, and the dataset a child view is showing
 *      (the Raw vs Clean inspector's dataset switch) is not in the hash at all
 *      unless something puts it there.
 *   3. Scraping the DOM would make grounding depend on markup: a class rename
 *      would silently change what the model is told, with no type error and no
 *      failing test.
 * So the state travels as a prop, is validated server-side, and every field is
 * optional — a client that omits `viewContext` retrieves exactly what it
 * retrieved before this field existed.
 */
/* ── The selected cell (added after v5; additive, like everything above) ───
 *
 * WHY THIS CARRIES COORDINATES AND NOT CONTENT.
 *
 * "Why is this cell red?" and "what's wrong with this row?" are the two most
 * natural questions to ask of the Raw vs Clean inspector, and neither one names
 * anything a retrieval system can key off: no defect code, no metric, no column,
 * often no verb. The only thing that makes them answerable is knowing WHICH cell
 * the reviewer clicked.
 *
 * There are two ways to tell the server that, and only one of them is safe:
 *
 *   1. POST the cell's values — raw, clean, status, defect code, the pipeline's
 *      explanation. Convenient, and wrong. Every one of those strings would be
 *      attacker-controlled text landing inside the model prompt, from a public
 *      URL, with no authentication in front of it. That is the injection channel
 *      `normaliseViewContext` already refuses to open for `dataset` and `metric`,
 *      and it would be a strange thing to close there and open here.
 *   2. POST the COORDINATES — dataset, row index, column — and let the server
 *      resolve the content itself out of `public/data/csv_diff.json`, a file it
 *      already ships and already trusts. Three small, exactly-validatable fields;
 *      nothing free-text reaches the prompt.
 *
 * This is (2). It is also strictly MORE capable than (1): because the server
 * loads the row rather than being handed one cell, the model sees every column of
 * that row — raw value, clean value, status, defect code — so "what's wrong with
 * this row?" is answerable from the same selection that answered "why is this
 * cell red?", and the answer can relate the clicked cell to its neighbours.
 *
 * WHY `rowIndex` IS THE SOURCE-ARRAY POSITION AND NOT `row_id`.
 * `row_id` is the natural key and it is NOT unique — the 15 TX-09 rows are exact
 * duplicates and share a transaction id, which is the whole point of that defect
 * class. A `row_id` would therefore be ambiguous for precisely the rows a
 * reviewer is most likely to click. The index into the dataset's `rows` array is
 * unique by construction, stable across sorting and filtering in the inspector
 * (which sorts a derived copy), and is the same number the server uses to look
 * the row up.
 */
export interface CellSelection {
  /** One of `stores` | `products` | `transactions`. Validated against that list. */
  dataset: string;
  /**
   * Zero-based index into `csv_diff.json[dataset].rows`. Validated as a
   * non-negative integer AND as being within range of the loaded file; an index
   * past the end resolves to nothing and the question is answered without cell
   * context rather than being rejected.
   */
  rowIndex: number;
  /**
   * The clicked column. Validated against that dataset's own `headers`.
   *
   * Optional and nullable on purpose: a row-level selection ("what's wrong with
   * this row?") is a legitimate state, and the row block is rendered either way.
   * A column that is not a header of that dataset invalidates the whole
   * selection, silently — a coordinate the server cannot verify is not one it
   * will act on.
   */
  column?: string | null;
}

export interface ViewContext {
  /**
   * The active view id — one of the ids in `config.ts:VIEWS`. Validated against
   * a server-side map; an unknown id is discarded rather than trusted, so a
   * stale or hand-crafted client cannot steer retrieval with a made-up view.
   */
  view: string;
  /** Defect code currently open in the Defect Explorer, e.g. "TX-03". */
  defect?: string | null;
  /** Active code allow-list, from `#defects/codes:TX-01,TX-02`. */
  codeFilter?: string[] | null;
  /** Dataset in focus: the profiled or inspected table (stores/products/transactions). */
  dataset?: string | null;
  /** Metric in focus on the Analytics view, e.g. "return_rate_by_store". */
  metric?: string | null;
  /**
   * The cell or row the reviewer has clicked in the Raw vs Clean inspector.
   * Coordinates only — see `CellSelection`. Absent for every other view, and
   * absent from a client built before this field existed, in which case
   * retrieval behaves exactly as it did before.
   */
  selection?: CellSelection | null;
}

export interface ChatRequestBody {
  question: string;
  /** Oldest first. Server truncates to the most recent `MAX_HISTORY_TURNS`. */
  history?: ChatTurn[];
  /**
   * What the reviewer is looking at. Optional and additive — see `ViewContext`.
   * Absent from a client built before this field existed, and absent is a valid
   * state rather than an error: retrieval then behaves exactly as before.
   */
  viewContext?: ViewContext;
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
  /**
   * One line naming the view the server grounded against, e.g.
   * `Analytics · metric in focus: return_rate_by_store`. Present only when the
   * request carried a `viewContext` the server recognised — so its absence is
   * itself informative: it means the page state did not reach retrieval.
   * Optional, therefore ignored by a client built against the earlier contract.
   */
  viewNote?: string;
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

/* ── Transport (added after v3; every field below is additive) ────────────
 *
 * WHY A "TRANSPORT" EXISTS AT ALL.
 * The Generative Language API now has two ways to ask a model a question, and
 * which one a given API key may use depends on how that key was minted:
 *
 *   `interactions`      POST /v1beta/interactions — the Interactions API, GA
 *                       since June 2026 and where new models launch. The model
 *                       name travels in the request BODY.
 *   `generateContent`   POST /v1beta/models/{model}:generateContent — the
 *                       original REST surface. Documented as legacy but fully
 *                       supported. The model name travels in the URL.
 *
 * The deployment this field was added for failed every live call with HTTP 400
 * on BOTH `models/{model}:generateContent` and `GET /v1beta/models` — the
 * latter a bare GET with no request body, so "malformed payload" could not be
 * the explanation. The key in that deployment is one of the new-style AI Studio
 * auth keys (they are now the default), and the working hypothesis is that such
 * keys are accepted by the Interactions API and refused by the legacy
 * `models/*` paths. The server therefore tries Interactions first and keeps
 * `generateContent` as an automatic fallback, and reports WHICH ONE answered —
 * because that single word is the observation that confirms or refutes the
 * hypothesis on a live deployment, and no amount of local testing can supply it.
 */
export type ChatTransport = "interactions" | "generateContent";

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
  /**
   * Which endpoint this attempt used. Optional so a client built against the
   * earlier contract is unaffected; present on every attempt the current server
   * records, because "gemini-3.6-flash failed" and "gemini-3.6-flash failed on
   * the legacy endpoint" are different findings.
   */
  transport?: ChatTransport;
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

  /* ── Transport fields. Additive; absent from an older server's payload. ── */

  /** The endpoint order tried on this request, after any instance-level caching. */
  transports?: ChatTransport[];
  /** The endpoint that produced the answer. Absent when nothing answered. */
  transport?: ChatTransport;
  /**
   * One line saying what the transport layer did and why — including which
   * endpoint was tried first and how it failed. Rendered verbatim.
   */
  transportNote?: string;
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
  /**
   * The endpoint that produced this text. Optional and ignored by older
   * clients. Shown in the provenance line next to the model name, because on a
   * deployment where one of the two endpoints refuses the key, "which endpoint
   * answered" is the single most useful fact about a successful call.
   */
  transport?: ChatTransport;
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

  /* ── Transport diagnosis. Additive, and the reason this probe exists. ────
   * `curl` against GET /api/chat now answers "which endpoint is this
   * deployment actually able to use?" — which is exactly the question the
   * 400-on-everything failure could not answer from the outside.
   */

  /** The endpoint that has answered on this instance. `null` until one has. */
  transport?: ChatTransport | null;
  /** The endpoint order the next question would try. */
  transports?: ChatTransport[];
  /** One line explaining the transport state, including anything retired. */
  transportNote?: string;
  /** The `GEMINI_TRANSPORT` env override, when set. */
  transportOverride?: ChatTransport;
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
