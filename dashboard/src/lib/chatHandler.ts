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
 *
 * ── AND WHY IT THEN GREW A TRANSPORT LAYER ────────────────────────────────
 * The model layer above was not enough. The live probe came back:
 *
 *   {"configured":true,"bundleAvailable":true,"discovery":"unavailable",
 *    "discoveryNote":"ListModels returned HTTP 400; the preference list was
 *     tried directly."}
 *
 * — i.e. the key reached the function, the bundle loaded, and BOTH upstream
 * calls returned HTTP 400: `POST models/{model}:generateContent` AND
 * `GET /v1beta/models`. ListModels carries no request body. There is nothing in
 * it to malform. A 400 on a bare GET is not a statement about a payload; it is
 * the credential being refused by that endpoint.
 *
 * THE HYPOTHESIS. The key on that deployment is one of the new-style Google AI
 * Studio **auth keys** (prefix `AQ.`; Google now mints these by default, where
 * the legacy format was `AIza…`). The Interactions API — GA since June 2026,
 * `POST /v1beta/interactions`, and where every new model now launches — accepts
 * them. The legacy `models/*` REST paths appear not to. Every observation above
 * is consistent with that, and with community reports; none of it can be
 * *proved* from a sandbox with no network, and this file does not pretend
 * otherwise.
 *
 * WHAT THIS FILE DOES ABOUT IT. Not "switch endpoints and hope". The endpoint
 * became a second dimension of the same fallback machinery that already existed
 * for models:
 *
 *   • **Interactions is the primary transport.** Every request is built for it
 *     first: model in the body, the grounded prompt as `input`, the system
 *     instruction re-sent (interaction-scoped config is not sticky), and
 *     `store: false` because this is a public demo and conversation text must
 *     not be retained in the project.
 *   • **generateContent remains an automatic fallback transport.** If
 *     Interactions fails in a way that implicates the ENDPOINT or the KEY TYPE
 *     — a 400/404 on the endpoint itself, as opposed to a content refusal or a
 *     quota error — the request falls through to the legacy path and continues
 *     there. This is not belt-and-braces for its own sake: a deployment holding
 *     an old `AIza` key must keep working, and if the hypothesis is inverted
 *     (auth keys refused by Interactions, accepted by generateContent) this code
 *     still answers the question. A fix that is only correct if a guess is
 *     correct is not a fix.
 *   • **The winning transport is cached per instance**, exactly as the winning
 *     model already is, so the chain is walked at most once per warm instance.
 *   • **The answer says which endpoint produced it.** `GET /api/chat`, the
 *     `resolution` on every response and the provenance line under every live
 *     answer all name the transport. THAT is the observation that settles the
 *     hypothesis: one live question whose answer says `via interactions` while
 *     the attempt log shows generateContent refused with 400 confirms it; the
 *     mirror image refutes it; both endpoints returning 400/API_KEY_INVALID
 *     means the key itself is bad and no transport helps.
 *
 * ListModels is deliberately NOT part of this decision. It is itself a legacy
 * `models/*` endpoint and will most likely keep failing for an auth key; its
 * failure degrades to blind mode as before and must never retire a candidate or
 * a transport.
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
  type ChatTransport,
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
 * No key in the URL: it goes in `x-goog-api-key`, same as every other call.
 */
const LIST_MODELS_URL = `${GEMINI_API_ROOT}/models?pageSize=1000`;

/**
 * The Interactions API. One fixed URL for every model — the model name travels
 * in the request body, which is the structural difference from the legacy path
 * and the reason a 404 here means something different from a 404 there (see
 * `classifyFailure`). No key in the URL, same rule as everywhere else.
 */
const INTERACTIONS_URL = `${GEMINI_API_ROOT}/interactions`;

function generateContentUrl(model: string): string {
  return `${GEMINI_API_ROOT}/models/${model}:generateContent`;
}

/* ── The transport preference chain ───────────────────────────────────────
 *
 * Same shape as the model chain above, one level up: an ordered list, an env
 * override that jumps the queue rather than replacing the list, and per-instance
 * caching of whichever entry actually worked.
 *
 * WHY INTERACTIONS FIRST. It is the GA surface, it is where new models launch,
 * `generateContent` is documented as legacy-but-supported, and — the operative
 * reason — it is the endpoint the live 400s point at. Trying it first costs a
 * healthy `AIza` deployment exactly one extra round-trip, once per warm
 * instance, after which the working transport is cached.
 *
 * WHY generateContent IS STILL IN THE LIST. Because the hypothesis might be
 * wrong. If auth keys turn out to be refused by Interactions instead, this list
 * self-corrects on the first request and the deployment still answers.
 */
const BASE_TRANSPORTS: readonly ChatTransport[] = ["interactions", "generateContent"] as const;

/** Accepts `interactions`, `generateContent`, `generate_content`, any case. */
function normaliseTransport(raw: string | undefined): ChatTransport | undefined {
  const value = (raw ?? "").trim().toLowerCase().replace(/[_-]/g, "");
  if (value === "interactions" || value === "interaction") return "interactions";
  if (value === "generatecontent" || value === "legacy") return "generateContent";
  return undefined;
}

/**
 * Server-side only, never `NEXT_PUBLIC_`. Lets an operator pin a transport from
 * the deployment dashboard with no rebuild — useful precisely because the
 * hypothesis this file encodes is unproven, and an operator who has watched one
 * endpoint work should be able to stop paying for the other one's round-trip.
 * An unrecognised value is ignored rather than fatal: a typo here must not take
 * the assistant down.
 */
export const TRANSPORT_OVERRIDE = normaliseTransport(process.env.GEMINI_TRANSPORT);

/** Override first, then the base list, de-duplicated, order preserved. */
export const TRANSPORT_PREFERENCE: ChatTransport[] = [
  ...(TRANSPORT_OVERRIDE ? [TRANSPORT_OVERRIDE] : []),
  ...BASE_TRANSPORTS,
].filter((t, i, all) => all.indexOf(t) === i);

/* ── Cost and abuse envelope ──────────────────────────────────────────────
 * Every one of these is a spend control as much as a safety control. The
 * bill for this route is (input tokens + output tokens) × requests, and each
 * constant below bounds one factor of that product.
 */

/** Per-IP request allowance and window. ~1 question every 15s sustained. */
const RATE_LIMIT = { limit: 20, windowMs: 5 * 60 * 1000 };
/** Upper bound on generated tokens — bounds the output half of the bill. */
const MAX_OUTPUT_TOKENS = 1400;
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
 *
 * Applies to the `generateContent` transport, whose `generationConfig`
 * documents `temperature`. See `INTERACTIONS_SEED` for what the Interactions
 * transport does instead, and why it does not simply send this too.
 */
const TEMPERATURE = 0.1;

/**
 * WHY THE INTERACTIONS CALL DOES NOT SEND A TEMPERATURE.
 *
 * The Interactions API's `generation_config` documents `max_output_tokens`,
 * `seed`, `stop_sequences`, `thinking_level`, `thinking_summaries` and the
 * media configs. It does not document `temperature`, `top_p` or
 * `candidate_count`. Google's JSON APIs reject unknown fields outright — 400
 * INVALID_ARGUMENT, "Cannot find field" — which is the exact failure class this
 * whole change exists to remove. Sending an undocumented field on the transport
 * we are switching TO in order to escape a 400 would be a remarkable way to
 * reintroduce one.
 *
 * So determinism is bought the way this API documents: a fixed `seed`. The
 * value is arbitrary and only has to be constant — the property wanted is "the
 * same question returns the same answer", which matters here for the same
 * reason `AS_OF_DATE` is pinned in the pipeline: a reviewer who asks twice and
 * gets two different numbers cannot tell which one to trust.
 *
 * If a live 400 ever names `seed` or `thinking_level`, drop them: they are
 * quality knobs, and the answer matters more than the knobs.
 */
const INTERACTIONS_SEED = 7;

/**
 * Thought tokens are billed as output tokens, and this is extraction over
 * supplied context — the reasoning that matters has already been done by the
 * retrieval layer. `low` rather than `minimal` because the task still involves
 * reconciling several quoted blocks, and rather than `high` because paying a
 * frontier model to deliberate over a quoted figure buys latency, not accuracy.
 */
const INTERACTIONS_THINKING_LEVEL = "low";

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
 * The transport that has actually answered on this instance. Null until one
 * has. Cached for exactly the same reason `resolvedModel` is: once an endpoint
 * has proved it works for this key, re-deriving that on every question is a
 * round-trip spent re-learning a constant.
 */
let resolvedTransport: ChatTransport | null = null;

/**
 * Transports retired after the ENDPOINT rejected the call (a 404, or a 400 that
 * is not about content, quota or the key string). Keyed by transport, value is
 * the human reason for `GET /api/chat`.
 *
 * An auth failure deliberately does NOT retire a transport: "this key is bad"
 * is not evidence about an endpoint, and retiring on it would hide the very
 * asymmetry — one endpoint accepting the key, the other refusing it — that this
 * layer exists to detect.
 */
const retiredTransports = new Map<ChatTransport, string>();

/**
 * Models retired after a model-specific rejection, with the reason. A retired
 * model is never tried again on this instance: it 404'd for this key once and
 * nothing about the next question changes that. Value is the human reason, so
 * `GET /api/chat` can show it.
 *
 * KEYED BY TRANSPORT, because model availability is a property of (project,
 * endpoint) and not of the model name alone: the Interactions API and the
 * legacy path do not publish the same catalogue, and a name the legacy endpoint
 * has never heard of is exactly the kind of name that launches on the new one.
 * Retiring `gemini-3.6-flash` everywhere because `generateContent` 404'd it
 * would be the model-selection bug this file already fixed, one level up.
 */
const retiredModels = new Map<ChatTransport, Map<string, string>>();

function retirementsFor(transport: ChatTransport): Map<string, string> {
  let map = retiredModels.get(transport);
  if (!map) {
    map = new Map<string, string>();
    retiredModels.set(transport, map);
  }
  return map;
}

function isModelRetired(transport: ChatTransport, model: string): boolean {
  return retirementsFor(transport).has(model);
}

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
  resolvedTransport = null;
  retiredTransports.clear();
  retiredModels.clear();
}

/** Read-only view of the instance's resolution state, for the status probe. */
export function __modelResolutionState(): {
  resolvedModel: string | null;
  resolvedTransport: ChatTransport | null;
  discovery: Discovery | null;
  retired: Array<{ model: string; reason: string }>;
  retiredTransports: Array<{ transport: ChatTransport; reason: string }>;
} {
  return {
    resolvedModel,
    resolvedTransport,
    discovery: cachedDiscovery,
    // Flattened across transports, with the transport named inside the reason:
    // the wire shape `{ model, reason }` predates the transport layer and older
    // clients still read it, so the extra dimension goes in the prose.
    retired: [...retiredModels.entries()].flatMap(([transport, models]) =>
      [...models.entries()].map(([model, reason]) => ({
        model,
        reason: `${reason} [${transport}]`,
      })),
    ),
    retiredTransports: [...retiredTransports.entries()].map(([transport, reason]) => ({
      transport,
      reason,
    })),
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
    // The body is read for classification only and then discarded — never
    // forwarded, never logged. WHY read it at all: ListModels carries no
    // request body, so a 400 here cannot mean "malformed request". It almost
    // always means the credential was refused, and saying so here saves the
    // operator from debugging a payload that was never the problem.
    let detail = "";
    try {
      detail = (await res.text()).slice(0, 600);
    } catch {
      /* Body unreadable; the status alone still classifies below. */
    }
    const keyRefused = isInvalidKeyFailure(res.status, detail);
    return unavailableDiscovery(
      `ListModels returned HTTP ${res.status}; the preference list was tried directly.` +
        (keyRefused
          ? " ListModels sends no request body, so this is the API key being refused," +
            " not a malformed request. " +
            AUTH_REMEDY
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

function planCandidates(discovery: Discovery | null, transport: ChatTransport): CandidatePlan {
  const notRetired = (m: string) => !isModelRetired(transport, m);

  /**
   * DISCOVERY DOES NOT APPLY TO THE INTERACTIONS TRANSPORT.
   *
   * ListModels reports `supportedGenerationMethods`, and the method it reports
   * is `generateContent`. It says nothing about which models the Interactions
   * API serves, and it is itself a legacy `models/*` endpoint — on the very
   * keys this transport exists for, it returns 400 and reports nothing at all.
   * Filtering the Interactions chain through it would let a legacy catalogue
   * veto a newer one, and would make a ListModels failure load-bearing for the
   * primary transport, which is precisely what must not happen.
   *
   * So the Interactions chain is always the plain preference list. That is not
   * a degraded mode here — it is the correct source of truth for this endpoint.
   */
  if (transport === "interactions") {
    return { candidates: MODEL_PREFERENCE.filter(notRetired), unavailable: [] };
  }

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

/*
 * The rules that used to live in a standalone `isModelSpecificFailure` now live
 * in `classifyFailure` below, because adding a transport dimension made them
 * mutually dependent: whether a 404 is about the model or about the endpoint
 * depends on which transport delivered it, and a 400 can only be read as
 * endpoint evidence if there is another endpoint left to try. Two functions
 * that must be consulted in a particular order, in a particular combination,
 * are one function.
 *
 * The rules themselves are unchanged: 404 on a URL carrying the model name is
 * the model; a 400 is only model-specific when the upstream text actually names
 * the model, because Google returns 400 for a genuinely malformed request too
 * and treating that as "wrong model" would walk the entire chain to report the
 * same malformed-request error at the end. Error bodies are read for these
 * booleans and then discarded; they are never forwarded or logged.
 */

/** Does the upstream error text mention this model by name? */
function namesModel(detail: string, model: string): boolean {
  return detail.toLowerCase().includes(model.toLowerCase());
}

/**
 * Is this failure a CONTENT refusal — the model or its safety layer declining
 * this particular prompt — rather than a statement about the endpoint?
 *
 * Used only to protect the transport decision. A prompt that a policy filter
 * rejects would be rejected identically on the other endpoint, so falling
 * through to it would cost a round-trip, produce the same refusal, and then
 * blame the wrong thing in the diagnosis.
 */
function isContentRefusal(detail: string): boolean {
  const text = detail.toLowerCase();
  return (
    text.includes("safety") ||
    text.includes("blocked") ||
    text.includes("prohibited") ||
    text.includes("recitation") ||
    text.includes("harm_category")
  );
}

/** Quota language, for the same protective purpose as `isContentRefusal`. */
function isQuotaFailure(status: number, detail: string): boolean {
  if (status === 429) return true;
  const text = detail.toLowerCase();
  return (
    text.includes("resource_exhausted") ||
    text.includes("quota") ||
    text.includes("rate limit") ||
    text.includes("billing")
  );
}

/* ── Transport-aware failure classification ───────────────────────────────
 *
 * One function, because the ORDER of these tests is the whole design and
 * scattering them makes that order invisible.
 */
type FailureClass =
  /** The key was refused. No candidate on this transport can do better. */
  | "auth"
  /** This model name is wrong for this endpoint. Try the next candidate. */
  | "model"
  /** This ENDPOINT rejected the call. Try the next transport. */
  | "endpoint"
  /** Quota or a service wobble. Retry, bounded and jittered. */
  | "transient"
  /** Deterministic and not attributable. Surface it now. */
  | "fatal";

/**
 * WHY A 404 MEANS DIFFERENT THINGS ON THE TWO TRANSPORTS.
 *
 * `generateContent` puts the model in the URL, so `POST
 * /v1beta/models/{model}:generateContent` → 404 is a statement about that name:
 * retire the model, try the next. `interactions` puts the model in the BODY and
 * has one fixed URL, so a 404 there is a statement about the URL — the endpoint
 * is not served for this caller — unless the error text names the model, in
 * which case it is about the model after all. Getting this backwards would make
 * the handler walk three model names against a URL that does not exist, or
 * abandon a working endpoint because one model name was stale.
 *
 * `hasAlternativeTransport` is what keeps this honest at the end of the chain.
 * A generic 400 with somewhere left to go is treated as evidence about the
 * endpoint and the request falls through; a generic 400 with nowhere left to go
 * is reported exactly as it was before this layer existed, so the pre-existing
 * "a 400 that does not name the model is surfaced, not walked down the chain"
 * behaviour is preserved rather than quietly turned into three round-trips.
 */
function classifyFailure(
  status: number,
  detail: string,
  model: string,
  modelInUrl: boolean,
  hasAlternativeTransport: boolean,
): FailureClass {
  // 1. The credential. Checked first because every candidate and every
  //    transport shares it, and because the Generative Language API answers a
  //    bad key with 400/API_KEY_INVALID rather than 401 (see below).
  if (isInvalidKeyFailure(status, detail)) return "auth";

  // 2. Quota and service faults are never evidence about an endpoint or a
  //    model. Retried in place.
  if (isTransient(status)) return "transient";

  // 3. An error that names the model is about the model, whichever transport
  //    delivered it.
  if ((status === 400 || status === 404) && namesModel(detail, model)) return "model";

  // 4. A bare 404: the model when the model is in the URL, the endpoint when it
  //    is not — and, when it is not and there is nowhere else to go, simply a
  //    failure, because "switch transport" is not an available conclusion.
  if (status === 404) {
    if (modelInUrl) return "model";
    return hasAlternativeTransport ? "endpoint" : "fatal";
  }

  // 5. Everything else in the 400/501 family. Content refusals and quota
  //    phrasing are excluded so they cannot masquerade as endpoint evidence.
  if (status === 400 || status === 501) {
    if (isContentRefusal(detail) || isQuotaFailure(status, detail)) return "fatal";
    return hasAlternativeTransport ? "endpoint" : "fatal";
  }

  return "fatal";
}

/**
 * Does this failure mean the API key itself was refused?
 *
 * WHY THIS IS NOT SIMPLY `status === 401 || status === 403`: the Generative
 * Language API returns **HTTP 400 with reason `API_KEY_INVALID`** for a
 * malformed, truncated or revoked key — not 401, and not 403. A handler that
 * classifies auth failures by status alone therefore reports a bad key as "the
 * request was rejected", which sends the operator hunting through their request
 * payload for a fault that lives in the API console.
 *
 * This was not hypothetical. A deployment with a correctly-plumbed key hit
 * exactly this: `generateContent` returned 400, and so did ListModels — a bare
 * GET with no body, nothing to malform. Two endpoints, one shared credential,
 * both 400. That is the signature this function now recognises.
 *
 * 401 and 403 are still treated as auth failures; they occur for restricted
 * keys and for projects without the API enabled.
 */
export function isInvalidKeyFailure(status: number, detail: string): boolean {
  if (status === 401 || status === 403) return true;
  if (status !== 400) return false;
  const text = detail.toLowerCase();
  return (
    text.includes("api_key_invalid") ||
    text.includes("api key not valid") ||
    text.includes("invalid api key") ||
    text.includes("api key expired")
  );
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
  const cause =
    status === 401
      ? "The key is missing, malformed or revoked."
      : status === 403
        ? "The key is valid but not permitted to make this call: it is restricted, or its " +
          "project does not have access to this API."
        : // 400 / API_KEY_INVALID. Called out explicitly because the status code
          // actively misleads here — everything else about a 400 says "your
          // request was wrong", and the request was fine.
          "The key string itself was refused (API_KEY_INVALID). This is usually a truncated " +
          "or mistyped value, stray whitespace or a newline captured on paste, or a key from " +
          "a different console than Google AI Studio. The request payload is not at fault.";
  return `Model API returned HTTP ${status} — the configured API key was rejected. ${cause}`;
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

/**
 * The transport-neutral request, built once per question.
 *
 * WHY THIS TYPE EXISTS. Two endpoints now serialise the same question, and the
 * one thing that must not vary between them is what the model is asked. The
 * grounding, the capped history and the system instruction are decided here,
 * once; a transport may only choose how to spell them on the wire. Without this
 * split, the fallback path is a second prompt nobody tests, and the day it runs
 * is the day the answers quietly change.
 */
interface GroundedRequest {
  systemInstruction: string;
  /** Oldest first, already capped and trimmed by the caller. */
  history: ChatTurn[];
  /** The retrieved context plus the question, as one user turn. */
  userTurn: string;
}

/**
 * What either transport reduces to. Everything downstream — the safety check,
 * the truncation notice, the numeric audit, the wire payload — reads this and
 * never a raw upstream shape.
 */
interface NormalisedAnswer {
  /** Answer text, with model reasoning stripped. May be empty. */
  text: string;
  /** Set when the PROMPT was refused before generation ever started. */
  blockReason?: string;
  /** Upstream finish/status token, for the "stopped" vs "finished" distinction. */
  finishReason?: string;
  /** The output was cut short at the token cap. */
  truncated: boolean;
  usage: { promptTokens?: number; responseTokens?: number; totalTokens?: number };
}

/* ── generateContent wire shapes (unchanged) ─────────────────────────────── */

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

/* ── Interactions wire shapes ─────────────────────────────────────────────
 *
 * Every field is optional and every reader below is defensive, on purpose. The
 * documented response is an `Interaction` resource whose `steps` array is a
 * CHRONOLOGICAL LOG — model thoughts, tool calls, then a `model_output` step —
 * and the answer lives inside the `model_output` step's `content`, NOT at the
 * top level. `interactions.create` returns only model-generated steps, but the
 * set of step kinds is open: an SDK-side `output_text` convenience exists, new
 * step types appear as features ship, and nothing about this schema promises
 * that the answer is at a fixed index. So the parser walks, filters and
 * concatenates rather than indexing, and an unrecognised step is skipped rather
 * than being allowed to break the answer.
 */

interface InteractionPart {
  type?: string;
  text?: string;
  thought?: boolean;
}

interface InteractionStep {
  type?: string;
  kind?: string;
  step_type?: string;
  content?: InteractionPart[] | string;
  parts?: InteractionPart[];
  text?: string;
}

interface InteractionShape {
  object?: string;
  status?: string;
  steps?: InteractionStep[];
  execution_steps?: InteractionStep[];
  /** SDK convenience; not promised on the REST wire, but honoured if present. */
  output_text?: string;
  outputText?: string;
  usage?: {
    total_input_tokens?: number;
    total_output_tokens?: number;
    total_tokens?: number;
    totalInputTokens?: number;
    totalOutputTokens?: number;
    totalTokens?: number;
  };
}

type CallOutcome =
  | {
      kind: "answered";
      model: string;
      transport: ChatTransport;
      answer: NormalisedAnswer;
      resolution: ModelResolution;
    }
  | {
      kind: "failed";
      errorKind: ChatErrorKind;
      message: string;
      remedy?: string;
      resolution: ModelResolution;
    };

/* ── Response parsing, one function per transport ─────────────────────────── */

/**
 * generateContent. Never returns `null`: any JSON object is a well-formed (if
 * empty) generateContent response, and the pre-existing behaviour — no
 * candidate means `empty_response`, not "wrong endpoint" — is preserved exactly.
 */
function parseGenerateContent(data: unknown): NormalisedAnswer {
  const body = (data ?? {}) as GeminiResponseShape;
  const candidate = body.candidates?.[0];
  const finishReason = candidate?.finishReason ?? "";

  // Gemini 3 models may return internal reasoning parts alongside the answer;
  // those carry `thought: true` and must not be shown to a reviewer as output.
  const text = (candidate?.content?.parts ?? [])
    .filter((p) => p.thought !== true && typeof p.text === "string")
    .map((p) => p.text as string)
    .join("")
    .trim();

  return {
    text,
    ...(body.promptFeedback?.blockReason ? { blockReason: body.promptFeedback.blockReason } : {}),
    ...(finishReason ? { finishReason } : {}),
    truncated: finishReason === "MAX_TOKENS",
    usage: {
      promptTokens: body.usageMetadata?.promptTokenCount,
      responseTokens: body.usageMetadata?.candidatesTokenCount,
      totalTokens: body.usageMetadata?.totalTokenCount,
    },
  };
}

/** Step kinds that carry the answer the reviewer is meant to read. */
const ANSWER_STEP_RE = /^(model_output|output|message|assistant|text)$/;
/**
 * Step kinds that are explicitly NOT the answer: the model's own reasoning, and
 * the tool traffic it generated on the way. Shown to a reviewer these would be
 * indistinguishable from the answer, which is the same mistake `thought: true`
 * parts already guard against on the other transport.
 */
const NON_ANSWER_STEP_RE = /(thought|reasoning|tool|function|code_execution|search|retrieval)/;
/** Part types that are metadata rather than prose. */
const NON_ANSWER_PART_RE = /(thought|reasoning|signature)/i;

function partsToText(parts: InteractionPart[]): string {
  return parts
    .filter(
      (p) =>
        p &&
        typeof p.text === "string" &&
        p.thought !== true &&
        !NON_ANSWER_PART_RE.test(String(p.type ?? "")),
    )
    .map((p) => p.text as string)
    .join("");
}

/** Text carried by one step, whichever of the documented shapes it uses. */
function stepText(step: InteractionStep): string {
  if (Array.isArray(step.content)) return partsToText(step.content);
  if (typeof step.content === "string") return step.content;
  if (Array.isArray(step.parts)) return partsToText(step.parts);
  if (typeof step.text === "string") return step.text;
  return "";
}

/**
 * Interactions. Returns `null` when the body is not an Interaction at all —
 * which is a statement about the ENDPOINT, not about this question, and is
 * therefore allowed to trigger a transport fallback upstream. Any other outcome
 * (including "an Interaction that produced no text") is a real answer object.
 */
function parseInteraction(data: unknown): NormalisedAnswer | null {
  if (!data || typeof data !== "object") return null;
  const body = data as InteractionShape;

  const steps = Array.isArray(body.steps)
    ? body.steps
    : Array.isArray(body.execution_steps)
      ? body.execution_steps
      : null;
  const convenience =
    typeof body.output_text === "string"
      ? body.output_text
      : typeof body.outputText === "string"
        ? body.outputText
        : null;

  // Recognition test. Deliberately generous: anything carrying steps, an
  // output_text, an interaction status or the `object: "interaction"` marker is
  // this API answering. Anything else is some other service on this URL, and
  // pretending to understand it would turn a transport problem into a silent
  // empty answer.
  const recognised =
    steps !== null ||
    convenience !== null ||
    typeof body.status === "string" ||
    body.object === "interaction";
  if (!recognised) return null;

  const kindOf = (s: InteractionStep) =>
    String(s.type ?? s.kind ?? s.step_type ?? "").toLowerCase();

  let text = "";
  if (steps) {
    // Pass 1: the documented answer steps, concatenated in order. `content` may
    // hold several text parts, and a multi-step interaction may emit more than
    // one output step, so this is a join and not a lookup.
    text = steps
      .filter((s) => ANSWER_STEP_RE.test(kindOf(s)))
      .map(stepText)
      .join("")
      .trim();

    // Pass 2: nothing matched the whitelist. Rather than report an empty answer
    // because a step kind was renamed or added, take any step that is not
    // recognisably reasoning or tool traffic and does carry text. This is the
    // "tolerate unexpected step kinds" clause, and it is why this function does
    // not assume a fixed index.
    if (!text) {
      text = steps
        .filter((s) => !NON_ANSWER_STEP_RE.test(kindOf(s)))
        .map(stepText)
        .join("")
        .trim();
    }
  }
  // Pass 3: an SDK-shaped payload with no steps included in the response.
  if (!text && convenience) text = convenience.trim();

  /* Interaction `status`, mapped onto the vocabulary the rest of this file
   * already speaks. `incomplete` is documented as "completed, but contains
   * incomplete results (e.g. hitting max_tokens)" — the same condition
   * generateContent calls MAX_TOKENS — and `budget_exceeded` is its
   * thinking-token cousin. Neither is an error; both mean the reviewer is
   * looking at a partial answer and must be told so. */
  const status = String(body.status ?? "").toLowerCase();
  const truncated = status === "incomplete" || status === "budget_exceeded";
  const finishReason = status && status !== "completed" ? status.toUpperCase() : "";

  return {
    text,
    ...(finishReason ? { finishReason } : {}),
    truncated,
    usage: {
      promptTokens: body.usage?.total_input_tokens ?? body.usage?.totalInputTokens,
      responseTokens: body.usage?.total_output_tokens ?? body.usage?.totalOutputTokens,
      totalTokens: body.usage?.total_tokens ?? body.usage?.totalTokens,
    },
  };
}

/* ── The two transports ───────────────────────────────────────────────────── */

interface TransportSpec {
  id: ChatTransport;
  /** Short human phrase used in messages and in the transport note. */
  label: string;
  /**
   * True when the model name is part of the URL. This single bit is what makes
   * a 404 mean "wrong model" on one transport and "wrong endpoint" on the
   * other — see `classifyFailure`.
   */
  modelInUrl: boolean;
  url: (model: string) => string;
  body: (req: GroundedRequest, model: string) => unknown;
  /** `null` means "this 2xx body is not this API's shape at all". */
  parse: (data: unknown) => NormalisedAnswer | null;
}

/**
 * The Interactions `input`, as a single string.
 *
 * WHY A STRING AND NOT A ROLE-TAGGED ARRAY. `input` accepts a Content, an array
 * of Content, an array of Step, an array of Turn, or a plain string — and the
 * only form the REST reference actually spells out is the string. The exact
 * wire schema of the array forms is an SDK-shaped detail that cannot be
 * verified from a sandbox with no network, and guessing at a request schema is
 * the failure this change exists to fix; a guess that 400s here would look
 * identical to the bug being repaired. So history is replayed statelessly as a
 * transcript inside the one documented form.
 *
 * (Stateless is not a compromise, either: `store: false` precludes
 * `previous_interaction_id`, so full history in the request is the only
 * multi-turn mechanism available — and the correct one for a public demo.)
 */
function buildInteractionsInput(req: GroundedRequest): string {
  if (req.history.length === 0) return req.userTurn;
  const transcript = req.history
    .map((t) => `${t.role === "user" ? "REVIEWER" : "ASSISTANT"}: ${t.text}`)
    .join("\n\n");
  return (
    "PRIOR TURNS IN THIS CONVERSATION (context only; the current question is at the end):\n\n" +
    `${transcript}\n\n----\n\n${req.userTurn}`
  );
}

const TRANSPORTS: Record<ChatTransport, TransportSpec> = {
  interactions: {
    id: "interactions",
    label: "Interactions endpoint",
    modelInUrl: false,
    url: () => INTERACTIONS_URL,
    body: (req, model) => ({
      model,
      input: buildInteractionsInput(req),
      /* Interaction-scoped, therefore re-sent on EVERY request. There is no
       * server-side session carrying it forward — and with `store: false` there
       * could not be. Omitting it on a follow-up would silently drop the
       * anti-fabrication contract for exactly the questions a reviewer asks
       * second. */
      system_instruction: req.systemInstruction,
      generation_config: {
        max_output_tokens: MAX_OUTPUT_TOKENS,
        seed: INTERACTIONS_SEED,
        thinking_level: INTERACTIONS_THINKING_LEVEL,
      },
      /* `store` defaults to TRUE — the service retains the interaction for later
       * retrieval. This is a public demo on someone's personal quota, and the
       * text being posted is a reviewer's questions plus a slice of the
       * pipeline bundle. None of that should accumulate in the project, and
       * nothing here ever reads an interaction back, so retention would be pure
       * liability. Explicit `false`, never omitted. */
      store: false,
    }),
    parse: parseInteraction,
  },
  generateContent: {
    id: "generateContent",
    label: "legacy generateContent endpoint",
    modelInUrl: true,
    url: generateContentUrl,
    body: (req) => ({
      systemInstruction: { parts: [{ text: req.systemInstruction }] },
      contents: [
        ...req.history.map((t) => ({ role: t.role, parts: [{ text: t.text }] })),
        { role: "user", parts: [{ text: req.userTurn }] },
      ],
      generationConfig: {
        temperature: TEMPERATURE,
        topP: 0.9,
        candidateCount: 1,
        maxOutputTokens: MAX_OUTPUT_TOKENS,
      },
    }),
    parse: parseGenerateContent,
  },
};

/**
 * Transport order for this request: the proven one first, then the rest of the
 * preference list, with anything the endpoint itself has already rejected on
 * this instance removed.
 *
 * The last-ditch clause mirrors `planCandidates`: if every transport has been
 * retired, try them all again rather than returning a failure without having
 * asked anything. A retirement is a cached observation, not a promise about the
 * future — Google can enable an endpoint for a project at any time.
 */
function orderTransports(): ChatTransport[] {
  const live = TRANSPORT_PREFERENCE.filter((t) => !retiredTransports.has(t));
  const pool = live.length > 0 ? live : [...TRANSPORT_PREFERENCE];
  if (resolvedTransport && pool.includes(resolvedTransport)) {
    return [resolvedTransport, ...pool.filter((t) => t !== resolvedTransport)];
  }
  return pool;
}

/**
 * Discovery, transport selection, candidate ordering, the fallback chains and
 * the retry loop, in one place, against ONE overall timeout budget.
 *
 * The nesting is: transports → candidate models → bounded retries. Each level
 * only moves outward when the level inside it has produced evidence that it
 * should — a retry for a transient fault, the next model for a name the
 * endpoint rejects, the next transport for a rejection of the endpoint itself.
 * All three share the single 25-second budget, because a reviewer waiting on an
 * answer does not care how many layers of fallback are spending their time.
 *
 * Returns a structured outcome rather than a `Response` so the caller can attach
 * the same `resolution` object to both the success and the failure payload —
 * the whole point of the exercise is that a reviewer can see what was tried
 * whichever way it went.
 */
async function callModelWithFallback(
  apiKey: string,
  req: GroundedRequest,
  deps: ChatDeps,
): Promise<CallOutcome> {
  const now = deps.now ?? (() => Date.now());
  const sleep = deps.sleep ?? ((ms: number) => new Promise((r) => setTimeout(r, ms)));
  const random = deps.random ?? Math.random;

  const deadline = now() + UPSTREAM_TIMEOUT_MS;
  const remaining = () => deadline - now();

  /* ListModels. Its failure is already degraded to blind mode inside
   * `discoverOnce`, and it is deliberately consulted only by the
   * generateContent plan (see `planCandidates`) — a 400 here must not retire a
   * candidate, must not retire a transport, and must not change which endpoint
   * is tried first. On an auth key it is expected to fail. */
  const discovery = await discoverOnce(apiKey, deps, remaining());

  const transports = orderTransports();
  const attempts: ModelAttempt[] = [];
  /** Models retired mid-chain. Kept separately from `attempts` so that a
   *  TRANSPORT being skipped never inflates the model-level `skipped` list. */
  const skippedModels: string[] = [];
  const transportNotes: string[] = [];
  /** Transports whose response said the KEY was refused. See below. */
  const keyRefusedBy: ChatTransport[] = [];

  // "Where are we" state, read by `resolution()` while the loops run.
  let transport: ChatTransport = transports[0];
  let plan: CandidatePlan = { candidates: [], unavailable: [] };
  let ordered: string[] = [];

  const describeTransports = (): string => {
    const head =
      `Transport order: ${transports.map((t) => TRANSPORTS[t].label).join(" → ")}. ` +
      "Interactions is POST /v1beta/interactions (model in the body); generateContent is " +
      "POST /v1beta/models/{model}:generateContent (model in the URL).";
    return [head, ...transportNotes].join(" ");
  };

  const resolution = (selected?: string, answeredOn?: ChatTransport): ModelResolution => ({
    ...(MODEL_OVERRIDE ? { requested: MODEL_OVERRIDE } : {}),
    preference: [...MODEL_PREFERENCE],
    candidates: [...ordered],
    ...(selected ? { selected } : {}),
    discovery: discovery.state,
    discoveryNote: discovery.note,
    attempts: [...attempts],
    skipped: [...new Set(skippedModels)],
    unavailable: [...plan.unavailable],
    transports: [...transports],
    ...(answeredOn ? { transport: answeredOn } : {}),
    transportNote: describeTransports(),
  });

  for (let ti = 0; ti < transports.length; ti += 1) {
    transport = transports[ti];
    const spec = TRANSPORTS[transport];
    /** Is there somewhere to fall through TO? Nothing is treated as evidence
     *  about an endpoint when there is no alternative endpoint left — in that
     *  position the failure is simply reported, exactly as it was before this
     *  layer existed. */
    const hasAlternativeTransport = ti < transports.length - 1;

    plan = planCandidates(discovery, transport);

    /**
     * The cached winner goes first. This is the "subsequent requests go straight
     * there" half of the fallback requirement: once something has answered, the
     * chain is not walked again unless that model itself stops working.
     */
    ordered =
      resolvedModel && !isModelRetired(transport, resolvedModel)
        ? [resolvedModel, ...plan.candidates.filter((m) => m !== resolvedModel)]
        : plan.candidates;

    /** Set when the ENDPOINT (not a model) rejected the call. */
    let switchTransport = false;

    for (const model of ordered) {
      if (switchTransport) break;
      let attemptCount = 0;

      while (attemptCount < RETRY_ATTEMPTS_PER_MODEL) {
        attemptCount += 1;

        const budget = remaining();
        if (budget <= 0) {
          attempts.push({
            model,
            transport,
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
          upstream = await deps.fetchImpl(spec.url(model), {
            method: "POST",
            headers: {
              "content-type": "application/json",
              // Key in a header on BOTH transports. Never `?key=`.
              "x-goog-api-key": apiKey,
            },
            body: JSON.stringify(spec.body(req, model)),
            signal: AbortSignal.timeout(budget),
          });
        } catch (err) {
          const name = (err as { name?: string })?.name ?? "";
          if (name === "TimeoutError" || name === "AbortError") {
            attempts.push({
              model,
              transport,
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
          // retry-policy note above. Not a transport fallback either: a DNS or
          // egress fault reaches the second endpoint exactly as it reached the
          // first, and pretending otherwise would double the wait before the
          // reviewer gets their scripted answer.
          attempts.push({
            model,
            transport,
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
          let data: unknown;
          try {
            data = await upstream.json();
          } catch {
            /* A 2xx that is not JSON. On the primary transport that is more
             * likely to be a proxy or an error page than this API, so it is
             * worth one look at the other endpoint before giving up. */
            if (hasAlternativeTransport) {
              const reason = `returned a 2xx that was not JSON (HTTP ${upstream.status})`;
              retiredTransports.set(transport, `the ${spec.label} ${reason}`);
              transportNotes.push(`The ${spec.label} ${reason}; the next transport was tried.`);
              attempts.push({
                model,
                transport,
                outcome: "skipped",
                status: upstream.status,
                attempts: attemptCount,
                reason: `${reason}; switching transport`,
              });
              switchTransport = true;
              break;
            }
            attempts.push({
              model,
              transport,
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

          const parsed = spec.parse(data);
          if (!parsed) {
            /* 2xx, valid JSON, and not this API's shape. Only the Interactions
             * parser can say this, and when it does the honest reading is "this
             * URL is not serving the API we think it is" — which is a transport
             * fact, not an answer. */
            const reason = "returned a response that is not in this API's shape";
            if (hasAlternativeTransport) {
              retiredTransports.set(transport, `the ${spec.label} ${reason}`);
              transportNotes.push(`The ${spec.label} ${reason}; the next transport was tried.`);
              attempts.push({
                model,
                transport,
                outcome: "skipped",
                status: upstream.status,
                attempts: attemptCount,
                reason: `${reason}; switching transport`,
              });
              switchTransport = true;
              break;
            }
            attempts.push({
              model,
              transport,
              outcome: "failed",
              status: upstream.status,
              attempts: attemptCount,
              reason,
            });
            return {
              kind: "failed",
              errorKind: "upstream_error",
              message: "The model API returned a response that could not be parsed.",
              resolution: resolution(),
            };
          }

          // The winners, cached for the life of the instance.
          resolvedModel = model;
          resolvedTransport = transport;
          attempts.push({
            model,
            transport,
            outcome: "answered",
            status: upstream.status,
            attempts: attemptCount,
            reason: attemptCount === 1 ? "answered" : `answered on attempt ${attemptCount}`,
          });
          return {
            kind: "answered",
            model,
            transport,
            answer: parsed,
            resolution: resolution(model, transport),
          };
        }

        const status = upstream.status;
        const detail = await readErrorDetail(upstream);
        const verdict = classifyFailure(
          status,
          detail,
          model,
          spec.modelInUrl,
          hasAlternativeTransport,
        );

        /* ── Key refused ───────────────────────────────────────────────────
         * Stop this transport: every candidate on it uses the same credential,
         * so walking the chain would produce the same rejection three more
         * times and bury the one fact that matters.
         *
         * But do NOT stop the request while another transport is untried, and
         * do NOT retire the transport. This is the crux of the auth-key
         * hypothesis: if one endpoint accepts a key that the other refuses,
         * hard-stopping on the first refusal would report "your key is invalid"
         * about a key that demonstrably works — which is precisely the wrong
         * diagnosis, and precisely the one this deployment was living with.
         * Only when EVERY transport has refused the key is the key itself the
         * defensible conclusion.
         *
         * This deliberately tests for more than 401/403: the Generative
         * Language API answers a malformed or revoked key with HTTP 400 /
         * API_KEY_INVALID. See `isInvalidKeyFailure`. */
        if (verdict === "auth") {
          keyRefusedBy.push(transport);
          attempts.push({
            model,
            transport,
            outcome: "failed",
            status,
            attempts: attemptCount,
            reason: `the API key was rejected by the ${spec.label}`,
          });

          if (hasAlternativeTransport) {
            transportNotes.push(
              `The ${spec.label} refused the API key (HTTP ${status}); the ` +
                `${TRANSPORTS[transports[ti + 1]].label} was tried next, because the two ` +
                "endpoints do not necessarily accept the same kind of key.",
            );
            switchTransport = true;
            break;
          }

          const bothRefused = new Set(keyRefusedBy).size > 1;
          return {
            kind: "failed",
            errorKind: "upstream_auth",
            message:
              `${authMessage(status)} ` +
              (bothRefused
                ? "Both the Interactions endpoint and the legacy generateContent endpoint refused " +
                  "this key, which rules out the endpoint and leaves the key itself."
                : `Reported by the ${spec.label}.`),
            remedy: AUTH_REMEDY,
            resolution: resolution(),
          };
        }

        /* ── The endpoint itself was rejected — change transport ───────────
         * This is the branch the whole change exists for. A 404 on a URL that
         * does not carry a model name, or a 400 that is neither a content
         * refusal nor a quota fault nor a key rejection, is a statement about
         * THIS ENDPOINT for THIS caller. The model chain cannot fix it and
         * retrying cannot fix it; the other endpoint might.
         *
         * Retired for the life of the instance so the second question does not
         * pay for the discovery the first one already did. */
        if (verdict === "endpoint") {
          const reason = `the ${spec.label} rejected the call (HTTP ${status})`;
          retiredTransports.set(transport, reason);
          transportNotes.push(
            `${reason.charAt(0).toUpperCase()}${reason.slice(1)} — not a content refusal and not ` +
              "a quota fault, so it implicates the endpoint or the key type rather than the " +
              `question. Fell through to the ${TRANSPORTS[transports[ti + 1]].label}.`,
          );
          attempts.push({
            model,
            transport,
            outcome: "skipped",
            status,
            attempts: attemptCount,
            reason: `${reason}; switching transport`,
          });
          switchTransport = true;
          break;
        }

        /* ── Model-specific — retire it and move down the chain. ─────────── */
        if (verdict === "model") {
          const reason =
            status === 404
              ? "not available to this API key's project (HTTP 404)"
              : "rejected by name by the model service (HTTP 400)";
          retirementsFor(transport).set(model, reason);
          if (resolvedModel === model) resolvedModel = null;
          skippedModels.push(model);
          attempts.push({
            model,
            transport,
            outcome: "skipped",
            status,
            attempts: attemptCount,
            reason,
          });
          break; // next candidate
        }

        /* ── Transient — bounded, jittered retry inside the same budget. ─── */
        if (verdict === "transient") {
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
            transport,
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

        /* Anything else (a 400 that does not name the model with no other
         * transport left to try, a 4xx we have no story for): deterministic.
         * Surface it now rather than spending the reviewer's time proving it
         * three times. */
        attempts.push({
          model,
          transport,
          outcome: "failed",
          status,
          attempts: attemptCount,
          reason: statusPhrase(status),
        });
        return {
          kind: "failed",
          errorKind: "upstream_error",
          message: `Model API returned HTTP ${status} — ${statusPhrase(status)}.`,
          resolution: resolution(),
        };
      }
    }

    /* Every candidate on this transport was retired, and the transport itself
     * never objected. If another endpoint is available it gets the same
     * question: a name the legacy catalogue has never heard of is exactly the
     * kind of name that exists on the newer one, and vice versa. */
    if (!switchTransport && hasAlternativeTransport && ordered.length > 0) {
      transportNotes.push(
        `No candidate model was usable on the ${spec.label}; the ` +
          `${TRANSPORTS[transports[ti + 1]].label} was tried with the same preference list.`,
      );
    }
  }

  /* Every candidate on every transport was retired. This is the "the project
   * has none of these models" case, and the message says exactly that, with the
   * list, because the fix is to set GEMINI_MODEL to a name the project has. */
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
  const transports = orderTransports();
  // The transport the NEXT question would use, which is what every field below
  // is describing. The candidate list is a property of the transport (see
  // `planCandidates`), so reporting one without the other would be misleading.
  const transport = transports[0];
  const plan = planCandidates(state.discovery, transport);
  const candidates =
    state.resolvedModel && !isModelRetired(transport, state.resolvedModel)
      ? [state.resolvedModel, ...plan.candidates.filter((m) => m !== state.resolvedModel)]
      : plan.candidates;

  const transportNote = state.resolvedTransport
    ? `Live answers on this instance are going over the ${TRANSPORTS[state.resolvedTransport].label}.`
    : `No live question has been answered on this instance yet; the ${TRANSPORTS[transport].label} ` +
      "will be tried first.";
  const retiredNote = state.retiredTransports
    .map((r) => ` Retired this instance: ${r.reason}.`)
    .join("");

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

    /* The transport half of the diagnosis. This is what makes one `curl`
     * against GET /api/chat answer the question the original failure could not:
     * not just "is a key present" but "which endpoint can this key actually
     * use". Still synchronous and network-free — it reports what the instance
     * has learned, and never spends a round-trip to learn more. */
    transport: state.resolvedTransport,
    transports,
    transportNote: `${transportNote}${retiredNote}`,
    ...(TRANSPORT_OVERRIDE ? { transportOverride: TRANSPORT_OVERRIDE } : {}),
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

  /* The transport-neutral request. What the model is asked is decided once,
   * here; how it is spelled on the wire is the transport's business. */
  const groundedRequest: GroundedRequest = {
    systemInstruction: SYSTEM_INSTRUCTION,
    history,
    userTurn,
  };

  /* 6. Call upstream: discovery, transport chain, candidate chain, bounded
   *    retry — all inside one budget. Key in a header on every one of those
   *    calls, never in a URL. */
  const outcome = await callModelWithFallback(apiKey, groundedRequest, deps);

  if (outcome.kind === "failed") {
    return fail(outcome.errorKind, redact(outcome.message, apiKey), {
      ...(outcome.remedy ? { remedy: outcome.remedy } : {}),
      resolution: outcome.resolution,
    });
  }

  /* 7. Interpret the answer. Both transports have already been reduced to a
   *    `NormalisedAnswer`, so everything below is transport-independent — which
   *    is the point: the safety check, the truncation notice and the numeric
   *    audit must behave identically whichever endpoint answered, or the
   *    fallback path becomes a second, untested product.
   *
   *    A safety block is NOT an error to swallow — the client says so
   *    explicitly rather than presenting silence as an answer. */
  const { blockReason, finishReason = "", truncated, text: answerText } = outcome.answer;

  if (blockReason) {
    return fail("blocked", `The model declined to answer this prompt (reason: ${blockReason}).`, {
      resolution: outcome.resolution,
    });
  }

  if (BLOCKING_FINISH_REASONS.has(finishReason)) {
    return fail(
      "blocked",
      `The model stopped before answering (finish reason: ${finishReason}).`,
      { resolution: outcome.resolution },
    );
  }

  if (!answerText) {
    return fail(
      "empty_response",
      truncated
        ? "The model hit its output limit before producing an answer. Try a narrower question."
        : "The model returned no text.",
      { resolution: outcome.resolution },
    );
  }

  const answer = truncated
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
    // And the endpoint it answered on. On a deployment where one of the two
    // refuses the key, this one word is the whole diagnosis.
    transport: outcome.transport,
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
    usage: outcome.answer.usage,
  });
}
