/**
 * Tests for the grounded assistant: the context selector, the scripted
 * (offline) answers, and the route handler against a MOCKED upstream.
 *
 * WHAT IS AND IS NOT TESTED HERE
 * ------------------------------
 * There is no test that calls Gemini. The build sandbox has no route to
 * generativelanguage.googleapis.com, and a test that faked a successful call
 * and then reported it as a passing integration would be exactly the kind of
 * dishonesty this whole submission argues against. What is tested is
 * everything on this side of the wire:
 *
 *   • the selector — deterministic, budget-bounded, and quoting real figures;
 *   • the scripted answers — no stale money, every number bundle-derived;
 *   • the handler — success, upstream failure, safety block, missing key,
 *     oversized body, malformed body, history cap, and the rate limiter,
 *     each with a fake `fetch` standing in for the model.
 *
 * The one thing only a live deployment can confirm is that the real endpoint
 * accepts this exact request shape. That is called out in the README.
 *
 * Run: npm test
 */

import fs from "node:fs";
import path from "node:path";

import {
  DEFAULT_CONTEXT_TOKEN_BUDGET,
  buildRunFacts,
  extractDefectCodes,
  selectContext,
} from "../src/lib/grounding";
import { buildScriptedAnswers, findScriptedAnswer } from "../src/lib/presets";
import { GEMINI_MODEL, handleChatPost, handleChatStatus, type ChatDeps } from "../src/lib/chatHandler";
import { __resetRateLimiter } from "../src/lib/rateLimit";
import { MAX_BODY_BYTES, MAX_HISTORY_TURNS, type ChatResponse, type ChatStatusResponse } from "../src/lib/chatContract";
import type { Bundle } from "../src/lib/types";

/* ── Tiny harness ─────────────────────────────────────────────────────────── */

let passed = 0;
const failures: string[] = [];

function check(name: string, condition: boolean, detail = ""): void {
  if (condition) {
    passed += 1;
    console.log(`  ok   ${name}`);
  } else {
    failures.push(`${name}${detail ? ` — ${detail}` : ""}`);
    console.log(`  FAIL ${name}${detail ? ` — ${detail}` : ""}`);
  }
}

function section(title: string): void {
  console.log(`\n${title}`);
}

/* ── Fixtures ─────────────────────────────────────────────────────────────── */

// Resolved from the package root (where `npm test` runs), not from __dirname:
// the compiled test lives under `.test-build/tests/`, two levels away from the
// data it needs, and hard-coding that depth would break the moment `outDir`
// changed.
const dataDir = path.resolve(process.cwd(), "public", "data");
const bundlePath = path.join(dataDir, "bundle.json");
const mockPath = path.join(dataDir, "bundle.mock.json");
const chosenPath = fs.existsSync(bundlePath) ? bundlePath : mockPath;
const bundle = JSON.parse(fs.readFileSync(chosenPath, "utf8")) as Bundle;

console.log(`bundle under test: ${path.basename(chosenPath)}`);

/** The figures the previous hardcoded assistant shipped. All stale. */
const STALE_FIGURES = ["170,816.34", "170816.34", "1,104.05", "1104.05", "11,668.00", "11668.00"];

function geminiOk(text: string): Response {
  return new Response(
    JSON.stringify({
      candidates: [{ content: { parts: [{ text }] }, finishReason: "STOP" }],
      usageMetadata: { promptTokenCount: 3000, candidatesTokenCount: 120, totalTokenCount: 3120 },
    }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
}

const FAKE_KEY = "AIza-TEST-KEY-DO-NOT-USE-0000000000";

interface Captured {
  url: string;
  headers: Record<string, string>;
  body: Record<string, unknown>;
}

function makeDeps(
  overrides: Partial<ChatDeps> & { captured?: Captured[]; respond?: () => Response } = {},
): ChatDeps {
  const captured = overrides.captured ?? [];
  return {
    getApiKey: overrides.getApiKey ?? (() => FAKE_KEY),
    getBundle: overrides.getBundle ?? (() => bundle),
    rateLimitEnabled: overrides.rateLimitEnabled ?? false,
    fetchImpl:
      overrides.fetchImpl ??
      (async (input: RequestInfo | URL, init?: RequestInit) => {
        captured.push({
          url: String(input),
          headers: (init?.headers ?? {}) as Record<string, string>,
          body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
        });
        return overrides.respond ? overrides.respond() : geminiOk("Grounded answer.");
      }),
  };
}

function post(body: unknown, headers: Record<string, string> = {}): Request {
  const payload = typeof body === "string" ? body : JSON.stringify(body);
  return new Request("https://example.test/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: payload,
  });
}

/* ── 1. Run facts ─────────────────────────────────────────────────────────── */

async function main(): Promise<void> {
  section("run facts");
  const facts = buildRunFacts(bundle);

  check("catalog carries 17 defect codes", facts.defectCodes.length === 17, `got ${facts.defectCodes.length}`);
  check(
    "coverage is matched === expected",
    facts.coverage.matched !== null && facts.coverage.matched === facts.coverage.expected,
    `${facts.coverage.matched} vs ${facts.coverage.expected}`,
  );
  check("every catalog code has a detected count", facts.defectCodes.every((c) => facts.detected[c] !== undefined));
  check(
    "reconciliation figures are all present",
    Object.values(facts.recon).every((v) => v !== null),
    JSON.stringify(facts.recon),
  );
  check(
    "returns_value keeps its negative sign",
    (facts.recon.returnsValue ?? 0) < 0,
    String(facts.recon.returnsValue),
  );

  /* ── 2. Context selector ────────────────────────────────────────────────── */

  section("context selector");

  const q1 = "Why did you preserve the TX-03 silent discounts instead of recomputing the total?";
  const c1 = selectContext(bundle, q1);

  check("names the defect code in the question", extractDefectCodes(q1).join(",") === "TX-03");
  check("preamble is always included", c1.includedIds[0] === "preamble");
  check("named defect dossier is included", c1.includedIds.includes("defect:TX-03"));
  check("source window for the named defect is included", c1.includedIds.includes("code:TX-03"));
  check(
    "context quotes the live discount total",
    c1.text.includes(String(facts.recon.discountTotal ?? "impossible")),
  );
  check(
    "context quotes the live net revenue",
    c1.text.includes(String(facts.recon.netRevenue ?? "impossible")),
  );
  check(
    "context carries real source lines with the DEFECT tag",
    /# DEFECT: TX-03/.test(c1.text),
  );
  check("context stays under budget", c1.approxTokens <= DEFAULT_CONTEXT_TOKEN_BUDGET, `${c1.approxTokens}`);
  check(
    "context is a small fraction of the bundle",
    c1.text.length < fs.statSync(chosenPath).size * 0.1,
    `${c1.text.length} chars vs ${fs.statSync(chosenPath).size} bytes`,
  );
  check(
    "no stale figure from the old hardcoded assistant appears",
    !STALE_FIGURES.some((f) => c1.text.includes(f)),
  );

  const c1again = selectContext(bundle, q1);
  check("selection is deterministic", c1.text === c1again.text);

  const tiny = selectContext(bundle, q1, { budgetTokens: 300 });
  check("a tiny budget still keeps the preamble", tiny.includedIds.includes("preamble"));
  check("a tiny budget drops everything else", tiny.droppedIds.length > 0, tiny.includedIds.join(","));

  const c2 = selectContext(bundle, "what is the return rate by store and which stores exceed the alert threshold?");
  check("metric retrieval finds return_rate_by_store", c2.includedIds.includes("metric:return_rate_by_store"));
  check("metric block carries its definition note", /definition \(numerator\/denominator/.test(c2.text));

  const c3 = selectContext(bundle, "hello");
  check("an unmatched question still gets the preamble", c3.includedIds.includes("preamble"));
  check(
    "an unmatched question is cheap",
    c3.approxTokens < 1200,
    `${c3.approxTokens} tokens`,
  );

  const c4 = selectContext(bundle, "how were orphaned store ids handled?");
  check(
    "term overlap retrieves the right defect without a code",
    c4.includedIds.includes("defect:TX-04"),
    c4.includedIds.join(","),
  );

  /* ── 3. Scripted answers ────────────────────────────────────────────────── */

  section("scripted (offline) answers");

  const answers = buildScriptedAnswers(bundle);
  check(
    "one answer per defect class plus metrics plus run summary",
    answers.length === 1 + facts.defectCodes.length + facts.metricIds.length,
    `${answers.length}`,
  );
  check("run summary is first (the honest fallback)", answers[0].label === "Run summary");

  const allText = answers.map((a) => `${a.answer}\n${a.talkingPoints.join("\n")}`).join("\n");
  check(
    "no stale figure survives anywhere in the scripted answers",
    !STALE_FIGURES.some((f) => allText.includes(f)),
    STALE_FIGURES.filter((f) => allText.includes(f)).join(","),
  );
  check(
    "the run summary quotes the current reconciliation",
    answers[0].answer.includes("168,957.80") || answers[0].answer.includes(String(facts.recon.grossListValue)),
  );

  const tx03 = answers.find((a) => a.defectCode === "TX-03");
  check("TX-03 has a scripted answer", Boolean(tx03));
  check("TX-03 snippet is real source with line numbers", /# DEFECT: TX-03/.test(tx03?.codeSnippet ?? ""));
  check("TX-03 code ref is file:line", /^src\/.+\.py:\d+$/.test(tx03?.codeRef ?? ""));

  check(
    "offline search resolves an explicit code",
    findScriptedAnswer(answers, "tell me about TX-06").defectCode === "TX-06",
  );
  check(
    "offline search falls back to the run summary, never to silence",
    findScriptedAnswer(answers, "zzzzz qqqqq").label === "Run summary",
  );

  /* ── 4. Route handler — status probe ────────────────────────────────────── */

  section("route handler: GET status");

  const statusOn = (await handleChatStatus(makeDeps()).json()) as ChatStatusResponse;
  check("reports configured when a key is present", statusOn.configured === true);
  check("reports the pinned model", statusOn.model === GEMINI_MODEL, statusOn.model);
  check("reports the bundle as available", statusOn.bundleAvailable === true);

  const statusRaw = await handleChatStatus(makeDeps()).text();
  check("status body never contains the key", !statusRaw.includes(FAKE_KEY), statusRaw);

  const statusOff = (await handleChatStatus(makeDeps({ getApiKey: () => undefined })).json()) as ChatStatusResponse;
  check("reports unconfigured when no key is set", statusOff.configured === false);

  /* ── 5. Route handler — POST paths ──────────────────────────────────────── */

  section("route handler: POST, mocked upstream");

  // 5a. Missing key.
  const noKeyRes = await handleChatPost(post({ question: "What is TX-03?" }), makeDeps({ getApiKey: () => "  " }));
  const noKeyBody = (await noKeyRes.json()) as ChatResponse;
  check("missing key returns 503", noKeyRes.status === 503, String(noKeyRes.status));
  check("missing key is typed not_configured", !noKeyBody.ok && noKeyBody.kind === "not_configured");

  // 5b. Happy path against the mock.
  const captured: Captured[] = [];
  const okRes = await handleChatPost(
    post({ question: "What did the pipeline do about TX-03?" }),
    makeDeps({ captured }),
  );
  const okBody = (await okRes.json()) as ChatResponse;
  check("success returns 200", okRes.status === 200);
  check("success is typed ok", okBody.ok === true);
  check("answer text is passed through", okBody.ok === true && okBody.answer === "Grounded answer.");
  check("model is reported", okBody.ok === true && okBody.model === GEMINI_MODEL);
  check(
    "context summary is reported to the client",
    okBody.ok === true && okBody.context.includedIds.includes("defect:TX-03"),
  );
  check(
    "usage is passed through when the upstream reports it",
    okBody.ok === true && okBody.usage?.totalTokens === 3120,
  );

  const call = captured[0];
  check("calls the pinned model endpoint", call.url === `https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent`, call.url);
  check("key travels in the x-goog-api-key header", call.headers["x-goog-api-key"] === FAKE_KEY);
  check("key is NOT in the URL", !call.url.includes(FAKE_KEY) && !call.url.includes("key="));
  check("a system instruction is sent", typeof call.body.systemInstruction === "object");
  check(
    "the system instruction forbids inventing numbers",
    JSON.stringify(call.body.systemInstruction).includes("NEVER state a number"),
  );
  check(
    "max output tokens are capped",
    (call.body.generationConfig as { maxOutputTokens?: number })?.maxOutputTokens === 1400,
  );
  check(
    "the prompt carries the retrieved context, not the whole bundle",
    JSON.stringify(call.body.contents).length < 60_000,
    String(JSON.stringify(call.body.contents).length),
  );

  // 5c. History cap.
  const longHistory = Array.from({ length: 40 }, (_, i) => ({
    role: i % 2 === 0 ? ("user" as const) : ("model" as const),
    text: `turn ${i}`,
  }));
  const capCaptured: Captured[] = [];
  await handleChatPost(post({ question: "and TX-04?", history: longHistory }), makeDeps({ captured: capCaptured }));
  const contents = capCaptured[0].body.contents as unknown[];
  check(
    "conversation length is capped server-side",
    contents.length === MAX_HISTORY_TURNS + 1,
    `${contents.length}`,
  );

  // 5d. Upstream non-2xx.
  const errRes = await handleChatPost(
    post({ question: "What is TX-03?" }),
    makeDeps({
      respond: () =>
        new Response(JSON.stringify({ error: { message: `API key ${FAKE_KEY} not valid` } }), { status: 400 }),
    }),
  );
  const errText = await errRes.text();
  check("upstream failure returns 502", errRes.status === 502, String(errRes.status));
  check("upstream failure is typed upstream_error", errText.includes('"upstream_error"'));
  check("upstream failure is not a stack trace", !errText.includes("at Object.") && !errText.includes("Error:"));
  check("an upstream body echoing the key is never forwarded", !errText.includes(FAKE_KEY), errText);

  // 5e. Network failure.
  const netRes = await handleChatPost(
    post({ question: "What is TX-03?" }),
    makeDeps({
      fetchImpl: async () => {
        throw new Error(`connect ECONNREFUSED generativelanguage.googleapis.com key=${FAKE_KEY}`);
      },
    }),
  );
  const netText = await netRes.text();
  check("network failure returns 502", netRes.status === 502);
  check("network failure never leaks the thrown message", !netText.includes(FAKE_KEY), netText);

  // 5f. Safety block.
  const blockRes = await handleChatPost(
    post({ question: "What is TX-03?" }),
    makeDeps({
      respond: () =>
        new Response(JSON.stringify({ promptFeedback: { blockReason: "SAFETY" } }), { status: 200 }),
    }),
  );
  const blockBody = (await blockRes.json()) as ChatResponse;
  check("a safety block returns 422", blockRes.status === 422, String(blockRes.status));
  check("a safety block is typed blocked", !blockBody.ok && blockBody.kind === "blocked");

  // 5g. Finish reason without text.
  const emptyRes = await handleChatPost(
    post({ question: "What is TX-03?" }),
    makeDeps({
      respond: () =>
        new Response(JSON.stringify({ candidates: [{ finishReason: "MAX_TOKENS", content: { parts: [] } }] }), {
          status: 200,
        }),
    }),
  );
  check("an empty candidate returns 502 empty_response", emptyRes.status === 502);

  // 5h. Thought parts are stripped.
  const thoughtRes = await handleChatPost(
    post({ question: "What is TX-03?" }),
    makeDeps({
      respond: () =>
        new Response(
          JSON.stringify({
            candidates: [
              {
                content: { parts: [{ text: "internal reasoning", thought: true }, { text: "public answer" }] },
                finishReason: "STOP",
              },
            ],
          }),
          { status: 200 },
        ),
    }),
  );
  const thoughtBody = (await thoughtRes.json()) as ChatResponse;
  check(
    "model reasoning parts are not shown to the reviewer",
    thoughtBody.ok === true && thoughtBody.answer === "public answer",
  );

  // 5i. Validation.
  const badJson = await handleChatPost(post("{not json"), makeDeps());
  check("malformed JSON returns 400", badJson.status === 400);

  const noQuestion = await handleChatPost(post({ history: [] }), makeDeps());
  check("a missing question returns 400", noQuestion.status === 400);

  const huge = "x".repeat(MAX_BODY_BYTES + 10);
  const tooBig = await handleChatPost(post({ question: huge }), makeDeps());
  check("an oversized body returns 413", tooBig.status === 413, String(tooBig.status));

  const longQuestion = await handleChatPost(post({ question: "y".repeat(5000) }), makeDeps());
  check("an over-long question returns 400", longQuestion.status === 400, String(longQuestion.status));

  const bundleGone = await handleChatPost(post({ question: "What is TX-03?" }), makeDeps({ getBundle: () => null }));
  check("an unreadable bundle returns 503 rather than throwing", bundleGone.status === 503);

  // 5j. Rate limit.
  __resetRateLimiter();
  const rlDeps = makeDeps({ rateLimitEnabled: true });
  let lastStatus = 0;
  for (let i = 0; i < 21; i += 1) {
    const res = await handleChatPost(
      post({ question: "What is TX-03?" }, { "x-forwarded-for": "203.0.113.9, 10.0.0.1" }),
      rlDeps,
    );
    lastStatus = res.status;
  }
  check("the 21st request from one address is rate limited", lastStatus === 429, String(lastStatus));

  const otherIp = await handleChatPost(
    post({ question: "What is TX-03?" }, { "x-forwarded-for": "198.51.100.4" }),
    rlDeps,
  );
  check("a different address is unaffected", otherIp.status === 200, String(otherIp.status));
  __resetRateLimiter();

  /* ── Result ─────────────────────────────────────────────────────────────── */

  console.log(`\n${passed} passed, ${failures.length} failed`);
  if (failures.length) {
    for (const f of failures) console.log(`  - ${f}`);
    process.exitCode = 1;
  }
}

void main();
