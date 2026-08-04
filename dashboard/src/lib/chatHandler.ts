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
 * `ChatDeps`: the API key, the bundle, the clock-free rate limiter and `fetch`.
 * The tests supply a fake `fetch` and a fake key; production supplies the real
 * ones. No test ever needs to reach generativelanguage.googleapis.com, and no
 * "successful" call is ever simulated as if it were real.
 *
 * ── KEY HANDLING (the non-negotiable part) ────────────────────────────────
 * `GEMINI_API_KEY` is read from `process.env` inside this module, which is
 * imported only by the route. It is:
 *   • never named with a NEXT_PUBLIC_ prefix, so Next's compiler will not inline
 *     it into any client bundle;
 *   • never placed in a URL (the Gemini REST API also accepts `?key=`; we use
 *     the `x-goog-api-key` header instead, so the secret cannot leak through
 *     redirect chains, proxy logs or an error message that echoes the URL);
 *   • never included in a response body — every upstream error is reduced to a
 *     status code and a short fixed phrase, and `redact()` is applied to the
 *     little free text that does escape, as a second line of defence;
 *   • never logged in full.
 * The only thing the browser can learn about it is the boolean
 * `configured: true | false` from `GET /api/chat`.
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
} from "./chatContract";
import { SYSTEM_INSTRUCTION, selectContext } from "./grounding";
import { clientKeyFrom, rateLimit } from "./rateLimit";
import type { Bundle } from "./types";

/**
 * The model. Pinned, not aliased ("gemini-flash-latest" would silently change
 * this artefact's behaviour under a reviewer months from now).
 */
export const GEMINI_MODEL = "gemini-3.6-flash";

const GEMINI_ENDPOINT =
  `https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent`;

/* ── Cost and abuse envelope ──────────────────────────────────────────────
 * Every one of these is a spend control as much as a safety control. The
 * bill for this route is (input tokens + output tokens) × requests, and each
 * constant below bounds one factor of that product.
 */

/** Per-IP request allowance and window. ~1 question every 15s sustained. */
const RATE_LIMIT = { limit: 20, windowMs: 5 * 60 * 1000 };
/** Upper bound on generated tokens — bounds the output half of the bill. */
const MAX_OUTPUT_TOKENS = 1400;
/** Abandon a slow upstream rather than holding a serverless function open. */
const UPSTREAM_TIMEOUT_MS = 25_000;
/**
 * Near-zero temperature. This is an extraction task over supplied context;
 * creativity here is indistinguishable from fabrication.
 */
const TEMPERATURE = 0.1;

export interface ChatDeps {
  /** Returns the raw key, or undefined/empty when unconfigured. */
  getApiKey: () => string | undefined;
  /** Returns the parsed bundle, or null when it cannot be read. */
  getBundle: () => Bundle | null;
  fetchImpl: typeof fetch;
  /** Disable to make tests deterministic without touching module state. */
  rateLimitEnabled: boolean;
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
  blocked: 422,
  timeout: 504,
  empty_response: 502,
};

function fail(kind: ChatErrorKind, message: string, retryAfterSeconds?: number): Response {
  const body: ChatFailure = { ok: false, kind, message };
  if (retryAfterSeconds !== undefined) body.retryAfterSeconds = retryAfterSeconds;
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (retryAfterSeconds !== undefined) headers["retry-after"] = String(retryAfterSeconds);
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

/* ── GET: capability probe ────────────────────────────────────────────────── */

/**
 * Tells the client whether a live answer is even possible, so the panel can
 * label itself "offline mode" on open rather than after a failed round-trip.
 * Returns a boolean about the key — never the key, never its length, never a
 * prefix of it.
 */
export function handleChatStatus(deps: ChatDeps = defaultDeps): Response {
  const body: ChatStatusResponse = {
    configured: Boolean(deps.getApiKey()?.trim()),
    model: GEMINI_MODEL,
    bundleAvailable: deps.getBundle() !== null,
  };
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
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
        verdict.retryAfterSeconds,
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

  /* 6. Call upstream. Key in a header, never in the URL. */
  let upstream: Response;
  try {
    upstream = await deps.fetchImpl(GEMINI_ENDPOINT, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-goog-api-key": apiKey,
      },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch (err) {
    const name = (err as { name?: string })?.name ?? "";
    if (name === "TimeoutError" || name === "AbortError") {
      return fail("timeout", `The model did not respond within ${UPSTREAM_TIMEOUT_MS / 1000}s.`);
    }
    // Deliberately not `String(err)`: a network error can carry the request URL
    // and, in some runtimes, request headers.
    return fail("upstream_error", "The model API could not be reached from the server.");
  }

  if (!upstream.ok) {
    // Status only. Upstream bodies quote request material back at you and are
    // not worth the leak risk; the status is enough to act on.
    const hint =
      upstream.status === 401 || upstream.status === 403
        ? "the configured API key was rejected"
        : upstream.status === 429
          ? "the upstream quota or rate limit was exceeded"
          : upstream.status >= 500
            ? "the model service reported an internal error"
            : "the request was rejected by the model service";
    return fail("upstream_error", `Model API returned HTTP ${upstream.status} — ${hint}.`);
  }

  let data: GeminiResponseShape;
  try {
    data = (await upstream.json()) as GeminiResponseShape;
  } catch {
    return fail("upstream_error", "The model API returned a response that could not be parsed.");
  }

  /* 7. Interpret the candidate. A safety block is NOT an error to swallow —
   *    the client says so explicitly rather than presenting silence as an answer. */
  const blockReason = data.promptFeedback?.blockReason;
  if (blockReason) {
    return fail("blocked", `The model declined to answer this prompt (reason: ${blockReason}).`);
  }

  const candidate = data.candidates?.[0];
  const finishReason = candidate?.finishReason ?? "";
  if (BLOCKING_FINISH_REASONS.has(finishReason)) {
    return fail("blocked", `The model stopped before answering (finish reason: ${finishReason}).`);
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
    );
  }

  const answer =
    finishReason === "MAX_TOKENS"
      ? `${answerText}\n\n[Answer truncated at the ${MAX_OUTPUT_TOKENS}-token output cap.]`
      : answerText;

  return ok({
    ok: true,
    answer: redact(answer, apiKey),
    model: GEMINI_MODEL,
    context: {
      approxTokens: context.approxTokens,
      budgetTokens: context.budgetTokens,
      includedIds: context.includedIds,
      droppedIds: context.droppedIds,
      mentionedCodes: context.mentionedCodes,
    },
    usage: {
      promptTokens: data.usageMetadata?.promptTokenCount,
      responseTokens: data.usageMetadata?.candidatesTokenCount,
      totalTokens: data.usageMetadata?.totalTokenCount,
    },
  });
}
