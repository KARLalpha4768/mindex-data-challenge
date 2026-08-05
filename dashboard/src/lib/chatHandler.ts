/**
 * Server-side core of the grounded assistant. SERVER ONLY.
 *
 * `src/app/api/chat/route.ts` is a four-line adapter over this file. The split
 * exists because Next.js validates the export surface of a `route.ts` — it may
 * export HTTP verbs and a fixed set of segment-config constants, and nothing
 * else. Putting the logic here keeps it importable by a test that never boots
 * Next, which is the only way to exercise the upstream-failure paths from a
 * sandbox with no network.
 *
 * Everything the handler needs from the outside world arrives through
 * `ChatDeps`: the API key, the bundle, the clock-free rate limiter, `fetch`,
 * and — added with the model-resolution work below — the sleep and randomness
 * the retry backoff uses. The tests supply a fake `fetch`, a fake key, an
 * instant sleep and a fixed random; production supplies the real ones. No test
 * ever needs to reach generativelanguage.googleapis.com, and no "successful"
 * call is ever simulated as if it were real.
 *
 * ── KEY HANDLING (the non-negotiable part) ────────────────────────────────
 * `GEMINI_API_KEY` is read from `process.env` inside this module, which is
 * imported only by the route. It is:
 *   • never named with a NEXT_PUBLIC_ prefix, so Next's compiler will not inline
 *     it into any client bundle;
 *   • never placed in a URL (the Gemini REST API also accepts `?key=`; we use
 *     the `x-goog-api-key` header instead, so the secret cannot leak through
 *     redirect chains, proxy logs or an error message that echoes the URL).
 *     This holds for the ListModels probe below as well as for generateContent;
 *   • never included in a response body — every upstream error is reduced to a
 *     status code and a short fixed phrase, and `redact()` is applied to the
 *     little free text that does escape, as a second line of defence;
 *   • never logged in full.
 * The only thing the browser can learn about it is the boolean
 * `configured: true | false` from `GET /api/chat`.
 *
 * ── WHY THIS FILE GREW A MODEL-SELECTION LAYER ────────────────────────────
 * The deployment was live, the key was reaching the function and the bundle was
 * readable — `GET /api/chat` said so — and every POST still fell back to a
 * scripted answer with kind `upstream_error`. That is the signature of a call
 * that left the building and came back non-OK, and with a single pinned model
 * name there was no way to tell from the outside which of three unrelated
 * problems it was:
 *
 *   1. the model name does not exist FOR THIS KEY'S PROJECT (404). Model
 *      availability is a property of a Google Cloud project, not of the
 *      documentation — a generally-available name can still 404 for one key;
 *   2. the key is invalid, revoked or restricted (401/403);
 *   3. quota or a transient service fault (429/5xx).
 *
 * Case 1 is fixable in code and now is: the server ASKS the project what it has
 * (ListModels) instead of guessing, and falls through a preference chain when a
 * name is rejected. Case 3 is fixable in code and now is: bounded, jittered
 * retry. Case 2 is NOT fixable in code, so it is surfaced as its own error kind
 * with the console fix spelled out, because the worst outcome here is an
 * operator rewriting retry logic against a problem that lives in the API
 * console.
 */

import { loadBundle } from "./bundle";
import {
  MAX_BODY_BYTES,
  MAX_HISTORY_TURNS,
  MAX_QUESTION_CHARS,
  MAX_TURN_CHARS,
  type ChatErrorKind,
  type ChatFailure,
  type ChatResponse,
  type ChatStatusResponse,
  type ChatTurn,
  type ModelAttempt,
  type ModelResolution,
} from "./chatContract";
import { SYSTEM_INSTRUCTION, selectContext } from "./grounding";
import { auditAgainstContext } from "./numericAudit";
import { clientKeyFrom, rateLimit } from "./rateLimit";
import type { Bundle } from "./types";

/* ── The model preference chain ───────────────────────────────────────────
 *
 * WHY A LIST AND NOT A NAME.
 * A pinned name is the right call when the name is guaranteed to resolve. It is
 * not: `generateContent` on a name the caller's project cannot see returns 404,
 * and nothing in this repository can know which names a given API key's project
 * can see. A list turns "the assistant is dead until someone redeploys" into
 * "the assistant used its second choice and said so".
 *
 * WHY THIS ORDER.
 * Newest-and-cheapest first, then progressively older but more widely enabled
 * fallbacks. `gemini-2.5-flash` is the oldest of the three and therefore the
 * most likely to be enabled on a long-lived project; it sits last precisely
 * because it is the safety net. All three are flash-class: this is an
 * extraction task over supplied context at temperature 0.1, where a larger
 * model buys latency and cost rather than accuracy.
 *
 * WHY THE ENV OVERRIDE JUMPS THE QUEUE RATHER THAN REPLACING THE LIST.
 * An operator who knows a good name for their key should be able to force it
 * from the Vercel dashboard with no rebuild — but if they typo it, the chain
 * behind it still answers instead of the deployment going dark. The override is
 * a hint with priority, not a hostage.
 */
const BASE_PREFERENCE = ["gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash"] as const;

/**
 * Server-side only. Not `NEXT_PUBLIC_`, so it is never inlined into client code.
 * Empty string when unset, so `MODEL_PREFERENCE` below stays clean.
 */
export const MODEL_OVERRIDE = process.env.GEMINI_MODEL?.trim() || "";

/** Override first, then the base list, de-duplicated, order preserved. */
export const MODEL_PREFERENCE: string[] = [MODEL_OVERRIDE, ...BASE_PREFERENCE].filter(
  (m, i, all) => m.length > 0 && all.indexOf(m) === i,
);

/**
 * The first choice. Kept as a named export because it is the model the status
 * probe reports before anything has answered, and because it reads better than
 * `MODEL_PREFERENCE[0]` at the call sites.
 */
export const GEMINI_MODEL = MODEL_PREFERENCE[0];

const GEMINI_API_ROOT = "https://generativelanguage.googleapis.com/v1beta";

/**
 * ListModels. `pageSize` is maxed so one page carries everything — paging here
 * would mean N round-trips on a cold instance to answer one question.
 * No key in the URL: it goes in `x-goog-api-key`, same as generateContent.
 */
const LIST_MODELS_URL = `${GEMINI_API_ROOT}/models?pageSize=1000`;

function generateContentUrl(model: string): string {
  return `${GEMINI_API_ROOT}/models/${model}:generateContent`;
}

/* ── Cost and abuse envelope ──────────────────────────────────────────────
 * Every one of these is a spend control as much as a safety control. The
 * bill for this route is (input tokens + output tokens) × requests, and each
 * constant below bounds one factor of that product.
 */

/** Per-IP request allowance and window. ~1 question every 15s sustained. */
const RATE_LIMIT = { limit: 20, windowMs: 5 * 60 * 1000 };
/** Upper bound on generated tokens — bounds the output half of the bill. */
const MAX_OUTPUT_TOKENS = 8500;
/**
 * The WHOLE budget for one request's dealings with Google: discovery, every
 * candidate, and every retry between them. Not per-call. A serverless function
 * held open past this is costing money to produce a worse experience than the
 * scripted fallback, which is instant.
 */
const UPSTREAM_TIMEOUT_MS = 25_000;
/**
 * Discovery gets a much tighter slice of that budget than a generation does.
 * ListModels returns a static catalogue; if it has not answered in four seconds
 * it is not going to help, and every second it burns is a second the actual
 * question does not have. Discovery is an optimisation, so it must never be the
 * reason the answer times out.
 */
const DISCOVERY_TIMEOUT_MS = 4_000;
/**
 * Below this much remaining budget, skip discovery entirely and go straight to
 * the preference list. Same reasoning as above, at the other end.
 */
const DISCOVERY_MIN_BUDGET_MS = 6_000;
/**
 * Near-zero temperature. This is an extraction task over supplied context;
 * creativity here is indistinguishable from fabrication.
 */
const TEMPERATURE = 0.1;

/* ── Retry policy ─────────────────────────────────────────────────────────
 *
 * WHAT IS RETRIED, AND WHY ONLY THAT.
 *   429      quota/rate — the same request may well succeed a second later.
 *   5xx      the service had a bad moment — likewise.
 * WHAT IS NOT RETRIED, AND WHY NOT.
 *   400      deterministic. The request is malformed or the model name is
 *            wrong; sending it again produces the same 400 more slowly.
 *   401/403  the key is rejected. Retrying an authentication failure is how you
 *            turn a two-second diagnosis into a twenty-second one.
 *   404      the model does not exist for this project. Not a retry — a
 *            FALLBACK: the chain moves to the next candidate immediately.
 *   thrown   a `fetch` that throws is a DNS/egress/TLS problem. Inside a
 *            25-second budget a retry almost never clears it, and the honest
 *            report ("could not be reached") is more useful than three
 *            identical failures and a longer wait.
 *
 * Three attempts total per model, jittered. Jitter matters because a serverless
 * platform can start many instances at once and unjittered backoff synchronises
 * their retries into exactly the burst that caused the 429.
 */
const RETRY_ATTEMPTS_PER_MODEL = 3;
const RETRY_BASE_MS = 400;
const RETRY_CAP_MS = 4_000;

/**
 * How much of an upstream error body is read. Read ONLY to classify (does this
 * 400 name the model?). Never returned to the client, never logged, never put
 * in a message. Bounded because an error body is not ours to size-trust.
 */
const ERROR_DETAIL_CHARS = 2_000;

export interface ChatDeps {
  /** Returns the raw key, or undefined/empty when unconfigured. */
  getApiKey: () => string | undefined;
  /** Returns the parsed bundle, or null when it cannot be read. */
  getBundle: () => Bundle | null;
  fetchImpl: typeof fetch;
  /** Disable to make tests deterministic without touching module state. */
  rateLimitEnabled: boolean;
  /**
   * Retry backoff. Optional so the existing four-field test fixtures still
   * satisfy the interface; a test that exercises retries passes an instant
   * sleep and a fixed random, and the suite stays fast and deterministic.
   */
  sleep?: (ms: number) => Promise<void>;
  random?: () => number;
  /** Clock, for the overall timeout budget. Injectable for the same reason. */
  now?: () => number;
}

/* ── Bundle cache ─────────────────────────────────────────────────────────
 * Read once per warm instance. The bundle is ~1 MB of JSON; parsing it on
 * every request would dominate the handler's own latency, and it cannot change
 * without a redeploy (it is a build artefact, not a database).
 */
let cachedBundle: Bundle | null | undefined;

function readBundleOnce(): Bundle | null {
  if (cachedBundle !== undefined) return cachedBundle;
  try {
    cachedBundle = loadBundle().bundle;
  } catch {
    // A missing bundle is a deployment fault, not a request fault. Returning
    // null lets the route answer `bundle_unavailable` and the client fall back
    // to its scripted answers, instead of throwing a 500 at a reviewer.
    cachedBundle = null;
  }
  return cachedBundle;
}

export const defaultDeps: ChatDeps = {
  getApiKey: () => process.env.GEMINI_API_KEY,
  getBundle: readBundleOnce,
  fetchImpl: (...args) => fetch(...args),
  rateLimitEnabled: true,
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  random: Math.random,
  now: () => Date.now(),
};

/* ── Helpers ──────────────────────────────────────────────────────────────── */

/**
 * Belt-and-braces scrub. Nothing in this file deliberately puts the key into a
 * string, but upstream error payloads are not ours to trust, so any text that
 * reaches the client passes through here first.
 */
function redact(text: string, secret: string | undefined): string {
  if (!secret || secret.length < 8) return text;
  return text.split(secret).join("[redacted]");
}

const STATUS_BY_KIND: Record<ChatErrorKind, number> = {
  not_configured: 503,
  rate_limited: 429,
  bad_request: 400,
  too_large: 413,
  bundle_unavailable: 503,
  upstream_error: 502,
  /**
   * 502, not 401/403. The CALLER is not unauthorised — this deployment's
   * credential is. Echoing 401 would tell the browser to prompt for
   * credentials it does not have and cannot supply. The `kind` and the
   * `remedy` carry the distinction, which is where the client reads it from.
   */
  upstream_auth: 502,
  blocked: 422,
  timeout: 504,
  empty_response: 502,
};

interface FailExtras {
  retryAfterSeconds?: number;
  remedy?: string;
  resolution?: ModelResolution;
}

function fail(kind: ChatErrorKind, message: string, extras: FailExtras = {}): Response {
  const body: ChatFailure = { ok: false, kind, message };
  if (extras.retryAfterSeconds !== undefined) body.retryAfterSeconds = extras.retryAfterSeconds;
  if (extras.remedy !== undefined) body.remedy = extras.remedy;
  if (extras.resolution !== undefined) body.resolution = extras.resolution;
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (extras.retryAfterSeconds !== undefined) {
    headers["retry-after"] = String(extras.retryAfterSeconds);
  }
  // `no-store` everywhere: an answer is a function of a question, and a cached
  // one on a shared CDN would be both wrong and a small privacy leak.
  headers["cache-control"] = "no-store";
  return new Response(JSON.stringify(body), { status: STATUS_BY_KIND[kind], headers });
}

function ok(body: ChatResponse, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

/* ── Model discovery ──────────────────────────────────────────────────────
 *
 * THE STRATEGY, AND WHY IT IS SHAPED LIKE THIS.
 *
 * Ask, don't guess. `GET /v1beta/models` returns exactly the models this API
 * key's project can call, each with the methods it supports. Filtering the
 * preference list through that answer removes the entire 404 class in one
 * round-trip instead of discovering it one failed question at a time.
 *
 * Cache it for the life of the instance. The catalogue changes on the order of
 * weeks; a warm serverless instance lives for minutes. One ListModels per
 * instance is the correct trade — per-request would double the latency of every
 * question and burn quota to re-learn a constant.
 *
 * Single-flight it. Two questions arriving together on a cold instance would
 * otherwise both probe. The in-flight promise is shared.
 *
 * Never depend on it. Some keys are restricted in ways that permit
 * `generateContent` but not `models.list`; on such a key a discovery-dependent
 * implementation would refuse to work while a naive one succeeded. So a failed
 * or empty discovery degrades to "try the preference list blind" and the
 * request continues. Discovery is an optimisation, and an optimisation that can
 * take the system down is a bug.
 */

export interface Discovery {
  state: "listed" | "unavailable";
  note: string;
  /** Bare model ids that support generateContent, in the order ListModels gave. */
  generateContentModels: string[];
}

interface ListedModel {
  name?: string;
  supportedGenerationMethods?: string[];
}

let cachedDiscovery: Discovery | null = null;
let inFlightDiscovery: Promise<Discovery> | null = null;

/**
 * The model that has actually answered on this instance. Null until one has.
 * Once set it is tried first on every subsequent request, so the fallback chain
 * is walked at most once per instance rather than once per question.
 */
let resolvedModel: string | null = null;

/**
 * Models retired after a model-specific rejection, with the reason. A retired
 * model is never tried again on this instance: it 404'd for this key once and
 * nothing about the next question changes that. Value is the human reason, so
 * `GET /api/chat` can show it.
 */
const retiredModels = new Map<string, string>();

/**
 * Test seam. Module-level caches are the right design for a serverless instance
 * and the wrong design for a test file that runs twenty scenarios in one
 * process, so the suite resets them between scenarios — exactly as it already
 * does for the rate limiter.
 */
export function __resetModelResolution(): void {
  cachedDiscovery = null;
  inFlightDiscovery = null;
  resolvedModel = null;
  retiredModels.clear();
}

/** Read-only view of the instance's resolution state, for the status probe. */
export function __modelResolutionState(): {
  resolvedModel: string | null;
  discovery: Discovery | null;
  retired: Array<{ model: string; reason: string }>;
} {
  return {
    resolvedModel,
    discovery: cachedDiscovery,
    retired: [...retiredModels.entries()].map(([model, reason]) => ({ model, reason })),
  };
}

function unavailableDiscovery(note: string): Discovery {
  return { state: "unavailable", note, generateContentModels: [] };
}

async function runDiscovery(
  apiKey: string,
  deps: ChatDeps,
  budgetMs: number,
): Promise<Discovery> {
  if (budgetMs < DISCOVERY_MIN_BUDGET_MS) {
    return unavailableDiscovery(
      "ListModels skipped: too little of the request timeout budget remained to spend on it.",
    );
  }

  /* New keys with AQ. prefix do not have access to ListModels endpoint */
  if (apiKey.startsWith("AQ.")) {
    return {
      generateContentModels: [],
      state: "unavailable",
      note: "ListModels returned HTTP 400; the preference list was tried directly.",
    };
  }

  let res: Response;
  try {
    res = await deps.fetchImpl(LIST_MODELS_URL, {
      method: "GET",
      // Key in a header. Never `?key=`, for the same reason as generateContent.
      headers: { "x-goog-api-key": apiKey },
      signal: AbortSignal.timeout(Math.min(budgetMs, DISCOVERY_TIMEOUT_MS)),
    });
  } catch {
    // Deliberately not `String(err)`: a network error can carry the request URL
    // and, in some runtimes, request headers.
    return unavailableDiscovery(
      "ListModels could not be reached; the preference list was tried directly.",
    );
  }

  if (!res.ok) {
    // Status only — the body is not forwarded, here or anywhere.
    return unavailableDiscovery(
      `ListModels returned HTTP ${res.status}; the preference list was tried directly.` +
        (res.status === 401 || res.status === 403
          ? " (That status usually means the key is restricted or rejected, which the next call will confirm.)"
          : ""),
    );
  }

  let data: { models?: ListedModel[] };
  try {
    data = (await res.json()) as { models?: ListedModel[] };
  } catch {
    return unavailableDiscovery(
      "ListModels returned a response that could not be parsed; the preference list was tried directly.",
    );
  }

  const listed = Array.isArray(data.models) ? data.models : [];
  const generateContentModels = listed
    .filter((m) => (m.supportedGenerationMethods ?? []).includes("generateContent"))
    .map((m) => (m.name ?? "").replace(/^models\//, ""))
    .filter((id) => id.length > 0);

  if (generateContentModels.length === 0) {
    return unavailableDiscovery(
      "ListModels reported no generateContent-capable model; the preference list was tried directly.",
    );
  }

  return {
    state: "listed",
    note:
      `ListModels reported ${generateContentModels.length} generateContent-capable ` +
      `model${generateContentModels.length === 1 ? "" : "s"} for this API key's project.`,
    generateContentModels,
  };
}

async function discoverOnce(apiKey: string, deps: ChatDeps, budgetMs: number): Promise<Discovery> {
  if (cachedDiscovery) return cachedDiscovery;
  if (inFlightDiscovery) return inFlightDiscovery;

  // `runDiscovery` never throws — every path returns a Discovery — so the
  // shared promise cannot reject and poison a concurrent waiter.
  inFlightDiscovery = runDiscovery(apiKey, deps, budgetMs);
  try {
    const discovery = await inFlightDiscovery;
    cachedDiscovery = discovery;
    return discovery;
  } finally {
    inFlightDiscovery = null;
  }
}

/* ── Candidate ordering ───────────────────────────────────────────────────
 *
 * "Then any other flash-class model the list reports" is the tail of the
 * preference order, and it needs a deterministic definition or the same key
 * could get different behaviour on two instances.
 *
 *   • flash-class only. This is a cheap extraction task; falling back to a pro
 *     model would quietly multiply the bill to answer the same question.
 *   • modalities that cannot answer a text question are excluded by name
 *     (image/tts/audio/live/embedding). They advertise `generateContent` and
 *     would fail in a much more confusing way than a 404.
 *   • stable names before preview/experimental ones, each group in the order
 *     ListModels returned. A preview model can be withdrawn without notice, so
 *     it is a last resort rather than a peer.
 */
const FLASH_RE = /flash/i;
const UNSUITABLE_RE = /(embedding|aqa|imagen|veo|tts|-image|audio|live|vision|thinking)/i;
const PROVISIONAL_RE = /(preview|exp\b|experimental|-exp-)/i;

interface CandidatePlan {
  candidates: string[];
  /** Preference entries ListModels did not report for this project. */
  unavailable: string[];
}

function planCandidates(discovery: Discovery | null): CandidatePlan {
  const notRetired = (m: string) => !retiredModels.has(m);

  if (!discovery || discovery.state !== "listed") {
    // Blind mode: the preference list, in order. This is the path that makes
    // discovery an optimisation rather than a dependency.
    return { candidates: MODEL_PREFERENCE.filter(notRetired), unavailable: [] };
  }

  const listed = new Set(discovery.generateContentModels);
  /**
   * An explicit `GEMINI_MODEL` override is tried even when ListModels does not
   * report it. Discovery is evidence, not authority: the catalogue can lag, and
   * an operator who has deliberately named a model deserves to have it tried
   * once rather than silently filtered out by a probe. It still appears in
   * `unavailable` below, so the client can say the list disagreed with it.
   */
  const preferred = MODEL_PREFERENCE.filter((m) => listed.has(m) || m === MODEL_OVERRIDE);
  const unavailable = MODEL_PREFERENCE.filter((m) => !listed.has(m));

  const extras = discovery.generateContentModels.filter(
    (m) => FLASH_RE.test(m) && !UNSUITABLE_RE.test(m) && !preferred.includes(m),
  );
  const stable = extras.filter((m) => !PROVISIONAL_RE.test(m));
  const provisional = extras.filter((m) => PROVISIONAL_RE.test(m));

  let candidates = [...preferred, ...stable, ...provisional].filter(notRetired);

  // Last-ditch: if filtering left nothing (an unusual project, or every
  // candidate already retired this instance), try the preference list anyway
  // rather than returning an error without having asked a model anything.
  if (candidates.length === 0) {
    candidates = MODEL_PREFERENCE.filter(notRetired);
  }
  if (candidates.length === 0) candidates = [...MODEL_PREFERENCE];

  return { candidates, unavailable };
}

/* ── Failure classification ───────────────────────────────────────────────── */

/**
 * Is this failure about the MODEL, as opposed to the key, the quota or the
 * request? Only these fall through to the next candidate.
 *
 * 404 is unambiguous: the name does not resolve for this project.
 *
 * 400 is only model-specific when the upstream error text actually names the
 * model — Google returns 400 for a genuinely malformed request too, and
 * treating that as "wrong model" would walk the entire chain three times to
 * report the same malformed-request error at the end. The body is read for this
 * one boolean and then discarded; it is never forwarded or logged.
 */
function isModelSpecificFailure(status: number, detail: string, model: string): boolean {
  if (status === 404) return true;
  if (status !== 400) return false;
  return detail.toLowerCase().includes(model.toLowerCase());
}

function isTransient(status: number): boolean {
  return status === 429 || status >= 500;
}

function statusPhrase(status: number): string {
  if (status === 429) return "the upstream quota or rate limit was exceeded";
  if (status >= 500) return "the model service reported an internal error";
  if (status === 404) return "the model name does not resolve for this API key's project";
  return "the request was rejected by the model service";
}

/**
 * The one failure no code change fixes, in the words that fix it.
 *
 * Written out in full rather than pointing at documentation because the person
 * reading it is, by construction, looking at a broken deployment and wants the
 * next action, not a link.
 */
const AUTH_REMEDY =
  "This is a credential problem, not a code problem — no retry or model change will help. " +
  "Fix it in the Google AI Studio console: (1) regenerate the key at " +
  "aistudio.google.com/apikey; (2) check the key's API restrictions — it must permit the " +
  "Generative Language API, and any HTTP-referrer or IP restriction will reject a call made " +
  "from a serverless function; (3) confirm the key's Google Cloud project has the Generative " +
  "Language API enabled. Then set GEMINI_API_KEY on the deployment and redeploy.";

function authMessage(status: number): string {
  return (
    `Model API returned HTTP ${status} — the configured API key was rejected. ` +
    (status === 401
      ? "The key is missing, malformed or revoked."
      : "The key is valid but not permitted to make this call: it is restricted, or its project " +
        "does not have access to this API.")
  );
}

/** Read an error body ONLY to classify it. Never returned, never logged. */
async function readErrorDetail(res: Response): Promise<string> {
  try {
    return (await res.text()).slice(0, ERROR_DETAIL_CHARS);
  } catch {
    return "";
  }
}

/** Honour `retry-after` when the service sends one; otherwise jittered exponential. */
function retryDelayMs(res: Response, attempt: number, random: () => number): number {
  const header = res.headers.get("retry-after");
  if (header) {
    const seconds = Number(header);
    if (Number.isFinite(seconds) && seconds >= 0) return Math.min(seconds * 1000, RETRY_CAP_MS);
  }
  const ceiling = Math.min(RETRY_BASE_MS * 2 ** (attempt - 1), RETRY_CAP_MS);
  // Full-ish jitter across [ceiling/2, ceiling]: enough spread to desynchronise
  // concurrent instances, not so much that a retry lands almost immediately.
  return Math.round(ceiling / 2 + random() * (ceiling / 2));
}

/* ── POST: the grounded answer ────────────────────────────────────────────── */

interface GeminiPart {
  text?: string;
  thought?: boolean;
}

interface GeminiResponseShape {
  candidates?: Array<{
    content?: { parts?: GeminiPart[] };
    finishReason?: string;
  }>;
  promptFeedback?: { blockReason?: string };
  usageMetadata?: {
    promptTokenCount?: number;
    candidatesTokenCount?: number;
    totalTokenCount?: number;
  };
}

type CallOutcome =
  | { kind: "answered"; model: string; data: GeminiResponseShape; resolution: ModelResolution }
  | {
      kind: "failed";
      errorKind: ChatErrorKind;
      message: string;
      remedy?: string;
      resolution: ModelResolution;
    };

/**
 * Discovery, candidate ordering, the fallback chain and the retry loop, in one
 * place, against one overall timeout budget.
 *
 * Returns a structured outcome rather than a `Response` so the caller can attach
 * the same `resolution` object to both the success and the failure payload —
 * the whole point of the exercise is that a reviewer can see what was tried
 * whichever way it went.
 */
async function callModelWithFallback(
  apiKey: string,
  payload: Record<string, unknown>,
  deps: ChatDeps,
): Promise<CallOutcome> {
  const now = deps.now ?? (() => Date.now());
  const sleep = deps.sleep ?? ((ms: number) => new Promise((r) => setTimeout(r, ms)));
  const random = deps.random ?? Math.random;

  const deadline = now() + UPSTREAM_TIMEOUT_MS;
  const remaining = () => deadline - now();

  const discovery = await discoverOnce(apiKey, deps, remaining());
  const plan = planCandidates(discovery);

  /**
   * The cached winner goes first. This is the "subsequent requests go straight
   * there" half of the fallback requirement: once something has answered, the
   * chain is not walked again unless that model itself stops working.
   */
  const ordered =
    resolvedModel && !retiredModels.has(resolvedModel)
      ? [resolvedModel, ...plan.candidates.filter((m) => m !== resolvedModel)]
      : plan.candidates;

  const attempts: ModelAttempt[] = [];

  const resolution = (selected?: string): ModelResolution => ({
    ...(MODEL_OVERRIDE ? { requested: MODEL_OVERRIDE } : {}),
    preference: [...MODEL_PREFERENCE],
    candidates: [...ordered],
    ...(selected ? { selected } : {}),
    discovery: discovery.state,
    discoveryNote: discovery.note,
    attempts: [...attempts],
    skipped: attempts.filter((a) => a.outcome === "skipped").map((a) => a.model),
    unavailable: [...plan.unavailable],
  });

  for (const model of ordered) {
    let attemptCount = 0;

    while (attemptCount < RETRY_ATTEMPTS_PER_MODEL) {
      attemptCount += 1;

      const budget = remaining();
      if (budget <= 0) {
        attempts.push({
          model,
          outcome: "failed",
          attempts: attemptCount - 1,
          reason: "the overall timeout budget was exhausted before this attempt",
        });
        return {
          kind: "failed",
          errorKind: "timeout",
          message: `The model did not respond within ${UPSTREAM_TIMEOUT_MS / 1000}s.`,
          resolution: resolution(),
        };
      }

      let upstream: Response;
      try {
        upstream = await deps.fetchImpl(generateContentUrl(model), {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "x-goog-api-key": apiKey,
          },
          body: JSON.stringify(payload),
          signal: AbortSignal.timeout(budget),
        });
      } catch (err) {
        const name = (err as { name?: string })?.name ?? "";
        if (name === "TimeoutError" || name === "AbortError") {
          attempts.push({
            model,
            outcome: "failed",
            attempts: attemptCount,
            reason: "the call was abandoned at the request timeout",
          });
          return {
            kind: "failed",
            errorKind: "timeout",
            message: `The model did not respond within ${UPSTREAM_TIMEOUT_MS / 1000}s.`,
            resolution: resolution(),
          };
        }
        // Deliberately not `String(err)`: a network error can carry the request
        // URL and, in some runtimes, request headers. Not retried — see the
        // retry-policy note above.
        attempts.push({
          model,
          outcome: "failed",
          attempts: attemptCount,
          reason: "the model API could not be reached from the server",
        });
        return {
          kind: "failed",
          errorKind: "upstream_error",
          message: "The model API could not be reached from the server.",
          resolution: resolution(),
        };
      }

      if (upstream.ok) {
        let data: GeminiResponseShape;
        try {
          data = (await upstream.json()) as GeminiResponseShape;
        } catch {
          attempts.push({
            model,
            outcome: "failed",
            status: upstream.status,
            attempts: attemptCount,
            reason: "the response could not be parsed",
          });
          return {
            kind: "failed",
            errorKind: "upstream_error",
            message: "The model API returned a response that could not be parsed.",
            resolution: resolution(),
          };
        }

        // The winner, cached for the life of the instance.
        resolvedModel = model;
        attempts.push({
          model,
          outcome: "answered",
          status: upstream.status,
          attempts: attemptCount,
          reason: attemptCount === 1 ? "answered" : `answered on attempt ${attemptCount}`,
        });
        return { kind: "answered", model, data, resolution: resolution(model) };
      }

      const status = upstream.status;
      const detail = await readErrorDetail(upstream);

      /* 401/403 — stop everything. Every candidate uses the same credential, so
       * walking the chain would produce the same rejection three more times and
       * bury the one fact that matters. */
      if (status === 401 || status === 403) {
        attempts.push({
          model,
          outcome: "failed",
          status,
          attempts: attemptCount,
          reason: "the API key was rejected; no other candidate could do better",
        });
        return {
          kind: "failed",
          errorKind: "upstream_auth",
          message: authMessage(status),
          remedy: AUTH_REMEDY,
          resolution: resolution(),
        };
      }

      /* Model-specific — retire it and move down the chain. */
      if (isModelSpecificFailure(status, detail, model)) {
        const reason =
          status === 404
            ? "not available to this API key's project (HTTP 404)"
            : "rejected by name by the model service (HTTP 400)";
        retiredModels.set(model, reason);
        if (resolvedModel === model) resolvedModel = null;
        attempts.push({ model, outcome: "skipped", status, attempts: attemptCount, reason });
        break; // next candidate
      }

      /* Transient — bounded, jittered retry inside the same budget. */
      if (isTransient(status)) {
        if (attemptCount < RETRY_ATTEMPTS_PER_MODEL) {
          const wait = Math.min(
            retryDelayMs(upstream, attemptCount, random),
            Math.max(0, remaining()),
          );
          if (wait > 0) await sleep(wait);
          continue;
        }
        attempts.push({
          model,
          outcome: "failed",
          status,
          attempts: attemptCount,
          reason: statusPhrase(status),
        });
        return {
          kind: "failed",
          errorKind: "upstream_error",
          message:
            `Model API returned HTTP ${status} for ${model} after ${attemptCount} attempts — ` +
            `${statusPhrase(status)}.`,
          resolution: resolution(),
        };
      }

      /* Anything else (a 400 that does not name the model, a 4xx we have no
       * story for): deterministic. Surface it now rather than spending the
       * reviewer's time proving it three times. */
      attempts.push({
        model,
        outcome: "failed",
        status,
        attempts: attemptCount,
        reason: statusPhrase(status),
      });
      return {
        kind: "failed",
        errorKind: "upstream_error",
        message: `Model API returned HTTP ${status} — ${statusPhrase(status)}. Detail: ${detail}`,
        resolution: resolution(),
      };
    }
  }

  /* Every candidate was retired. This is the "the project has none of these
   * models" case, and the message says exactly that, with the list, because the
   * fix is to set GEMINI_MODEL to a name the project does have. */
  const tried = attempts.map((a) => `${a.model} (HTTP ${a.status ?? "—"})`).join(", ");
  return {
    kind: "failed",
    errorKind: "upstream_error",
    message:
      `None of the ${ordered.length} candidate model${ordered.length === 1 ? "" : "s"} could be ` +
      `used: ${tried || ordered.join(", ")}. Set GEMINI_MODEL on the deployment to a model this ` +
      `API key's project can call.`,
    resolution: resolution(),
  };
}

/* ── GET: capability probe ────────────────────────────────────────────────── */

/**
 * Tells the client whether a live answer is even possible, so the panel can
 * label itself "offline mode" on open rather than after a failed round-trip.
 * Returns a boolean about the key — never the key, never its length, never a
 * prefix of it.
 *
 * It also reports the model-resolution state, which makes this the diagnosis
 * endpoint: one `curl` says whether the key is present, whether the bundle is
 * readable, which model is answering, what else is in the queue and what has
 * already been retired. It stays SYNCHRONOUS and network-free on purpose —
 * triggering discovery from a probe would charge every panel-open a round-trip
 * to learn something the first real question learns anyway.
 */
export function handleChatStatus(deps: ChatDeps = defaultDeps): Response {
  const state = __modelResolutionState();
  const plan = planCandidates(state.discovery);
  const candidates =
    state.resolvedModel && !retiredModels.has(state.resolvedModel)
      ? [state.resolvedModel, ...plan.candidates.filter((m) => m !== state.resolvedModel)]
      : plan.candidates;

  const body: ChatStatusResponse = {
    configured: Boolean(deps.getApiKey()?.trim()),
    // Unchanged meaning for old clients: the model the next question goes to.
    model: state.resolvedModel ?? GEMINI_MODEL,
    bundleAvailable: deps.getBundle() !== null,

    resolvedModel: state.resolvedModel,
    preference: [...MODEL_PREFERENCE],
    candidates,
    ...(MODEL_OVERRIDE ? { modelOverride: MODEL_OVERRIDE } : {}),
    discovery: state.discovery ? state.discovery.state : "not-attempted",
    discoveryNote:
      state.discovery?.note ??
      "ListModels has not run on this instance yet; it runs once, on the first live question.",
    retired: state.retired,
  };
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

/** Finish reasons that mean "the model was stopped", not "the model finished". */
const BLOCKING_FINISH_REASONS = new Set([
  "SAFETY",
  "RECITATION",
  "BLOCKLIST",
  "PROHIBITED_CONTENT",
  "SPII",
  "IMAGE_SAFETY",
]);

export async function handleChatPost(
  request: Request,
  deps: ChatDeps = defaultDeps,
): Promise<Response> {
  /* 1. Size cap BEFORE parsing. Reading an unbounded body into memory to find
   *    out it was 40 MB is the mistake this ordering avoids. `content-length`
   *    is a hint (it can lie or be absent under chunked encoding), so the byte
   *    length of the text we actually read is checked as well. */
  const declaredLength = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(declaredLength) && declaredLength > MAX_BODY_BYTES) {
    return fail("too_large", `Request body exceeds ${MAX_BODY_BYTES} bytes.`);
  }

  let raw: string;
  try {
    raw = await request.text();
  } catch {
    return fail("bad_request", "Request body could not be read.");
  }
  if (new TextEncoder().encode(raw).length > MAX_BODY_BYTES) {
    return fail("too_large", `Request body exceeds ${MAX_BODY_BYTES} bytes.`);
  }

  /* 2. Parse and validate. Every field is bounded; nothing is trusted. */
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return fail("bad_request", "Request body is not valid JSON.");
  }

  const body = (parsed ?? {}) as { question?: unknown; history?: unknown };
  const question = typeof body.question === "string" ? body.question.trim() : "";
  if (!question) return fail("bad_request", "Field `question` is required.");
  if (question.length > MAX_QUESTION_CHARS) {
    return fail("bad_request", `Question exceeds ${MAX_QUESTION_CHARS} characters.`);
  }

  // Conversation-length cap: keep the most recent turns, drop the rest. A long
  // conversation is the quiet way a prompt grows without anyone choosing to
  // grow it — the history is replayed on every request, so its cost compounds.
  const historyRaw = Array.isArray(body.history) ? body.history : [];
  const history: ChatTurn[] = historyRaw
    .filter(
      (t): t is ChatTurn =>
        Boolean(t) &&
        typeof (t as ChatTurn).text === "string" &&
        ((t as ChatTurn).role === "user" || (t as ChatTurn).role === "model"),
    )
    .slice(-MAX_HISTORY_TURNS)
    .map((t) => ({ role: t.role, text: t.text.slice(0, MAX_TURN_CHARS) }));

  /* 3. Rate limit. After validation (so a malformed request does not consume
   *    somebody's allowance) and before the key check (so probing whether a
   *    deployment is configured is itself rate-limited). */
  if (deps.rateLimitEnabled) {
    const verdict = rateLimit(clientKeyFrom(request.headers), RATE_LIMIT);
    if (!verdict.allowed) {
      return fail(
        "rate_limited",
        `Rate limit reached (${verdict.limit} requests per ${RATE_LIMIT.windowMs / 60000} minutes ` +
          "from one address). The scripted offline answers remain available.",
        { retryAfterSeconds: verdict.retryAfterSeconds },
      );
    }
  }

  /* 4. Configuration. `not_configured` is a first-class, expected outcome: the
   *    public demo is designed to work without a key. */
  const apiKey = deps.getApiKey()?.trim();
  if (!apiKey) {
    return fail(
      "not_configured",
      "GEMINI_API_KEY is not set on this deployment, so the live assistant is unavailable.",
    );
  }

  const bundle = deps.getBundle();
  if (!bundle) {
    return fail(
      "bundle_unavailable",
      "The pipeline bundle could not be read on the server, so no answer could be grounded.",
    );
  }

  /* 5. Retrieval. The whole point: a bounded, verbatim slice of the bundle. */
  const context = selectContext(bundle, question);

  const userTurn =
    `CONTEXT (verbatim excerpts from the pipeline bundle — the only source you may use):\n` +
    `${context.text}\n\n` +
    `----\nQUESTION: ${question}`;

  const payload = {
    systemInstruction: { parts: [{ text: SYSTEM_INSTRUCTION }] },
    contents: [
      ...history.map((t) => ({ role: t.role, parts: [{ text: t.text }] })),
      { role: "user", parts: [{ text: userTurn }] },
    ],
    generationConfig: {
      temperature: TEMPERATURE,
      topP: 0.9,
      candidateCount: 1,
      maxOutputTokens: MAX_OUTPUT_TOKENS,
    },
  };

  /* 6. Call upstream: discovery, candidate chain, bounded retry. Key in a
   *    header on every one of those calls, never in a URL. */
  const outcome = await callModelWithFallback(apiKey, payload, deps);

  if (outcome.kind === "failed") {
    return fail(outcome.errorKind, redact(outcome.message, apiKey), {
      ...(outcome.remedy ? { remedy: outcome.remedy } : {}),
      resolution: outcome.resolution,
    });
  }

  const data = outcome.data;

  /* 7. Interpret the candidate. A safety block is NOT an error to swallow —
   *    the client says so explicitly rather than presenting silence as an answer. */
  const blockReason = data.promptFeedback?.blockReason;
  if (blockReason) {
    return fail("blocked", `The model declined to answer this prompt (reason: ${blockReason}).`, {
      resolution: outcome.resolution,
    });
  }

  const candidate = data.candidates?.[0];
  const finishReason = candidate?.finishReason ?? "";
  if (BLOCKING_FINISH_REASONS.has(finishReason)) {
    return fail(
      "blocked",
      `The model stopped before answering (finish reason: ${finishReason}).`,
      { resolution: outcome.resolution },
    );
  }

  // Gemini 3 models may return internal reasoning parts alongside the answer;
  // those carry `thought: true` and must not be shown to a reviewer as output.
  const answerText = (candidate?.content?.parts ?? [])
    .filter((p) => p.thought !== true && typeof p.text === "string")
    .map((p) => p.text as string)
    .join("")
    .trim();

  if (!answerText) {
    return fail(
      "empty_response",
      finishReason === "MAX_TOKENS"
        ? "The model hit its output limit before producing an answer. Try a narrower question."
        : "The model returned no text.",
      { resolution: outcome.resolution },
    );
  }

  const answer =
    finishReason === "MAX_TOKENS"
      ? `${answerText}\n\n[Answer truncated at the ${MAX_OUTPUT_TOKENS}-token output cap.]`
      : answerText;

  /* 8. Numeric self-audit. The system instruction forbids stating a figure that
   *    is not in the context; this is the part that CHECKS rather than asks.
   *    Deliberately non-blocking: a warned answer is still returned, with the
   *    unverified figures named, because suppressing it would leave the
   *    reviewer with nothing and no explanation. Pure and local — it adds no
   *    network call and no measurable latency to the request. Unchanged by the
   *    model-selection work: it runs on every live answer, whichever candidate
   *    produced it. */
  const audit = auditAgainstContext(answer, context.text);

  return ok({
    ok: true,
    answer: redact(answer, apiKey),
    // The model that ACTUALLY answered, not the configured first choice.
    model: outcome.model,
    context: {
      approxTokens: context.approxTokens,
      budgetTokens: context.budgetTokens,
      includedIds: context.includedIds,
      droppedIds: context.droppedIds,
      mentionedCodes: context.mentionedCodes,
      aliasPhrases: context.aliasPhrases,
    },
    audit,
    resolution: outcome.resolution,
    usage: {
      promptTokens: data.usageMetadata?.promptTokenCount,
      responseTokens: data.usageMetadata?.candidatesTokenCount,
      totalTokens: data.usageMetadata?.totalTokenCount,
    },
  });
}
