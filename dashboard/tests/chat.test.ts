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
  ALIAS_RULES,
  DEFAULT_CONTEXT_TOKEN_BUDGET,
  buildRunFacts,
  extractDefectCodes,
  matchAliases,
  selectContext,
} from "../src/lib/grounding";
import {
  auditAnswer,
  auditAgainstContext,
  indexBundleNumbers,
  indexNumbers,
} from "../src/lib/numericAudit";
import {
  INTERVIEW_QUESTIONS,
  buildScriptedAnswers,
  findScriptedAnswer,
  resolveInterviewAnswer,
} from "../src/lib/presets";
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
  /**
   * The two independent controls must BOTH be present.
   *
   * `line_level_delta` is a per-row identity, `aggregate_delta` an aggregate
   * one, computed by different routes precisely so that one can fail while the
   * other passes. They replaced a single `reconciliation_delta` that was an
   * algebraic tautology — incapable of failing, and therefore worthless as a
   * control.
   *
   * This assertion previously ran against a stale bundle that still carried
   * only the superseded column, and was left deliberately red rather than made
   * green by reading one delta into both fields — which would have reported two
   * independent checks where one ran. Regenerating the bundle from the current
   * pipeline resolved it at source. `reconciliationDelta` may now be null; it
   * is the superseded column and its absence is correct.
   */
  const REQUIRED_RECON: Array<keyof typeof facts.recon> = [
    "grossListValue",
    "discountTotal",
    "grossSalesNetOfDiscount",
    "returnsValue",
    "netRevenue",
    "lineLevelDelta",
    "aggregateDelta",
  ];
  const missingRecon = REQUIRED_RECON.filter((k) => facts.recon[k] === null);
  check(
    "reconciliation figures are all present",
    missingRecon.length === 0,
    `absent from analytics.metrics.revenue_reconciliation.rows[0]: ${missingRecon.join(", ")}. ` +
      `Regenerate the bundle from solution/ — a stale bundle carries the superseded ` +
      `reconciliation_delta instead. Present: ${JSON.stringify(facts.recon)}`,
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

  /**
   * The set is: the run summary, the five hand-written trade-off answers, one
   * answer per defect class, one per metric.
   *
   * This assertion previously read `1 + defects + metrics` and had gone stale
   * against `tradeoffAnswers()`, which was added to `presets.ts` afterwards —
   * so it failed on an artefact that was working correctly. The count is now
   * derived from the same three groups the builder assembles, and the coverage
   * claim ("one per defect class") is asserted directly rather than inferred
   * from an arithmetic identity, which is what let it drift in the first place.
   */
  check(
    "every defect class has a scripted answer",
    facts.defectCodes.every((code) =>
      answers.some((a) => a.label.startsWith(code) && a.defectCode === code),
    ),
    facts.defectCodes.filter((c) => !answers.some((a) => a.label.startsWith(c))).join(","),
  );
  check(
    "every metric has a scripted answer",
    facts.metricIds.every((id) => answers.some((a) => a.label === `Metric ${id}`)),
  );
  check(
    "the answer set is run summary + trade-offs + defects + metrics, with nothing else",
    answers.length === 1 + 5 + facts.defectCodes.length + facts.metricIds.length,
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

  /**
   * The CATALOG-DERIVED TX-03 answer, selected by label rather than by
   * `defectCode`.
   *
   * Two answers carry `defectCode === "TX-03"`: the one generated from the
   * defect catalog (label "TX-03 …") and the hand-written trade-off card
   * ("⚡ TX-03 Discount Preservation"), which sorts first and whose snippet is
   * an illustrative three lines rather than a slice of the repository. The
   * assertion below is about the generated answer quoting real source, so it
   * now names which of the two it means instead of taking whichever came first.
   */
  const tx03 = answers.find((a) => a.defectCode === "TX-03" && a.label.startsWith("TX-03"));
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

  /* ── 6. Alias expansion in retrieval ────────────────────────────────────── */

  section("alias expansion");

  // The miss that motivated the table: not one content word in common with the
  // TX-03 dossier, which says "reconciliation", "discount" and "extended_amount".
  const addUp = selectContext(bundle, "Why don't the numbers add up?");
  check(
    "'why don't the numbers add up' retrieves TX-03",
    addUp.includedIds.includes("defect:TX-03"),
    addUp.includedIds.join(","),
  );
  check(
    "the phrase that fired is reported for inspection",
    addUp.aliasPhrases.includes("add up"),
    addUp.aliasPhrases.join(","),
  );

  const ordinaryPhrasings: Array<[string, string]> = [
    ["the zip code on one store looks wrong", "defect:ST-01"],
    ["what happens on a guest checkout with no account?", "defect:TX-06"],
    ["how do you handle refunds?", "defect:TX-10"],
    ["one store has a blank region", "defect:ST-03"],
    ["which row wins when the same store appears twice?", "defect:ST-02"],
    ["some rows point at a store that doesn't exist", "defect:TX-04"],
    ["a product appears twice with a different price", "defect:PR-02"],
    ["is the date format parsed wrong anywhere?", "defect:TX-01"],
    ["who are the biggest spenders?", "metric:top_customers_lifetime"],
    ["what is the average order value by region?", "metric:aov_by_region"],
    ["show me the busiest stores in the last 30 days", "metric:top_stores_recent_30d"],
  ];
  for (const [question, wanted] of ordinaryPhrasings) {
    const sel = selectContext(bundle, question);
    check(
      `ordinary phrasing retrieves ${wanted}: "${question}"`,
      sel.includedIds.includes(wanted),
      sel.includedIds.join(","),
    );
  }

  // An alias must inform ranking, never override an explicit instruction: a
  // typed code is still retrieved with full prose and its source window.
  const explicit = selectContext(bundle, "what did you do about ST-02?");
  check(
    "an explicitly named code still wins outright",
    explicit.includedIds.includes("defect:ST-02") && explicit.includedIds.includes("code:ST-02"),
    explicit.includedIds.join(","),
  );

  check(
    "every alias rule names at least one real target",
    ALIAS_RULES.every((r) =>
      [...(r.codes ?? []), ...(r.metrics ?? [])].every(
        (t) => facts.defectCodes.includes(t) || facts.metricIds.includes(t),
      ),
    ),
    ALIAS_RULES.flatMap((r) => [...(r.codes ?? []), ...(r.metrics ?? [])])
      .filter((t) => !facts.defectCodes.includes(t) && !facts.metricIds.includes(t))
      .join(","),
  );
  check(
    "no alias rule is empty",
    ALIAS_RULES.every((r) => r.phrases.length > 0 && (r.codes ?? r.metrics ?? []).length > 0),
  );
  check("aliases do not fire on an unrelated question", matchAliases("hello there").phrases.length === 0);

  /* ── 7. The ten interview questions ─────────────────────────────────────── */

  section("interview questions: retrieval");

  /**
   * What each question's model answer in INTERVIEW_QA.md actually cites. If
   * retrieval does not put these in front of the model, the model cannot answer
   * without inventing — which is the failure this table exists to prevent.
   */
  const REQUIRED: Record<number, string[]> = {
    1: ["defect:TX-03", "metric:revenue_reconciliation"],
    2: ["defect:PR-02"],
    3: ["defect:TX-10", "metric:return_rate_by_store"],
    4: ["metric:mom_growth_by_category"],
    5: ["defect:TX-04", "defect:TX-05", "defect:TX-07", "defect:TX-08", "defect:TX-09"],
    6: ["defect:TX-06", "metric:top_customers_lifetime"],
    7: ["defect:ST-03", "metric:aov_by_region"],
    8: ["defect:ST-01"],
    9: ["defect:TX-01"],
    10: ["metric:revenue_reconciliation"],
  };

  check("all ten interview questions are wired in", INTERVIEW_QUESTIONS.length === 10);
  check(
    "they are in ranked order",
    INTERVIEW_QUESTIONS.every((q, i) => q.rank === i + 1),
  );

  for (const item of INTERVIEW_QUESTIONS) {
    const sel = selectContext(bundle, item.question);
    const missing = (REQUIRED[item.rank] ?? []).filter((id) => !sel.includedIds.includes(id));
    check(
      `Q${item.rank} retrieves what its answer needs (${(REQUIRED[item.rank] ?? []).join(" ")})`,
      missing.length === 0,
      `missing ${missing.join(",")} — got ${sel.includedIds.join(",")}`,
    );
    check(
      `Q${item.rank} stays inside the context budget`,
      sel.approxTokens <= DEFAULT_CONTEXT_TOKEN_BUDGET,
      `${sel.approxTokens}`,
    );
    check(
      `Q${item.rank} resolves to a scripted answer for offline mode`,
      resolveInterviewAnswer(answers, item).answer.length > 0,
    );
  }

  check(
    "the offline hint for Q4 is the month-over-month metric, not the run summary",
    resolveInterviewAnswer(answers, INTERVIEW_QUESTIONS[3]).label === "Metric mom_growth_by_category",
    resolveInterviewAnswer(answers, INTERVIEW_QUESTIONS[3]).label,
  );
  check(
    "the offline hint for Q10 is the reconciliation metric",
    resolveInterviewAnswer(answers, INTERVIEW_QUESTIONS[9]).label === "Metric revenue_reconciliation",
    resolveInterviewAnswer(answers, INTERVIEW_QUESTIONS[9]).label,
  );

  /* ── 8. Numeric self-audit ──────────────────────────────────────────────── */

  section("numeric self-audit: the rules");

  const CTX =
    "net_revenue = $158,044.29 | discount_total = $961.48 | returns_value = -9952.03 | " +
    "fact_sales=474 | quarantined=38 | unit_return_rate_pct=13.73 | " +
    "return_rate_alert_threshold=0.1 | revenue_tie_out_cents=0 | as_of_date 2026-06-02";
  const ctxIndex = indexNumbers(CTX, "retrieved-context");
  const audit = (answer: string) => auditAnswer(answer, ctxIndex);

  check(
    "a figure present in context passes",
    audit("Net revenue is $158,044.29.").verdict === "pass",
  );
  check(
    "the same figure without a currency symbol or separators passes",
    audit("Net revenue is 158044.29.").verdict === "pass",
  );
  check(
    "a rounded restatement passes (158,044.3 vs 158044.29)",
    audit("Net revenue is about $158,044.3.").verdict === "pass",
  );
  check(
    "sign is not part of the comparison (returns are negative in the bundle)",
    audit("Returns were $9,952.03.").verdict === "pass",
  );
  check(
    "a percentage carried as a ratio in context still passes",
    audit("The alert threshold is 10%.").verdict === "pass",
  );

  // ── The adversarial case. This MUST be flagged. ──
  const invented = audit("Net revenue is $159,132.44 across the fact table.");
  check("a plausible but absent figure is flagged", invented.verdict === "warn", JSON.stringify(invented.figures));
  check(
    "the flagged figure is named, with its surrounding claim",
    invented.figures.some((f) => f.verdict === "unverified" && f.text.includes("159,132.44")),
  );
  check(
    "a near-miss on the cents is flagged, not rounded away",
    audit("Net revenue is $158,044.31.").verdict === "warn",
  );
  const inventedCount = audit("The pipeline quarantined 47 rows.");
  check("an invented row count is flagged", inventedCount.verdict === "warn");

  // ── Exemptions: the false positives that would make the badge useless. ──
  const exempt = audit(
    "TX-03 is tagged in src/cleaning/transactions.py:214 and was resolved on 2026-06-02. " +
      "Three stores breach the threshold, and store S006 is one of them. See line 42.",
  );
  check("codes, ids, dates and line refs are not flagged", exempt.verdict !== "warn", JSON.stringify(exempt.figures));
  check("exemptions are counted and reported by kind", exempt.exemptCount > 0 && Object.keys(exempt.exemptByKind).length > 0);
  check(
    "a small cardinal used as prose is exempt",
    audit("Two of them were imputed and 12 remained.").verdict !== "warn",
  );
  check(
    "but a bare integer above the ceiling is checked",
    audit("Thirteen were imputed: 13 rows.").verdict === "warn",
  );
  check(
    "a unit defeats the small-integer exemption",
    audit("It cost $7.").verdict === "warn",
  );
  check(
    "an answer with no figures reports no-figures rather than passing silently",
    audit("The context does not contain that metric.").verdict === "no-figures",
  );

  // ── Derivation: rule 4. ──
  const derived = audit(
    "Recomputing would raise net revenue from $158,044.29 to $159,005.77 — $961.48 that no " +
      "customer paid, a 0.61% overstatement.",
  );
  check("arithmetic over figures the answer shows is not flagged", derived.verdict === "pass", JSON.stringify(derived.figures));
  check(
    "a derived figure is reported as derived, not as verified",
    derived.figures.some((f) => f.verdict === "derived" && f.text.includes("159,005.77")),
    JSON.stringify(derived.figures.map((f) => `${f.text}:${f.verdict}`)),
  );
  check(
    "the derivation names its operands",
    derived.figures.some((f) => f.verdict === "derived" && /158044\.29/.test(f.note)),
    JSON.stringify(derived.figures.map((f) => f.note)),
  );
  check(
    "derivation cannot launder an invention: ungrounded operands do not count",
    audit("$1,234.56 plus $2,000.00 gives $3,234.56.").unverified === 3,
    JSON.stringify(audit("$1,234.56 plus $2,000.00 gives $3,234.56.").figures.map((f) => f.verdict)),
  );

  /* ── 9. Verifier: legitimate answers must not be flagged ────────────────── */

  section("numeric self-audit: false-positive rate on legitimate answers");

  /**
   * Answers of the shape the live model actually produces, each audited against
   * the context that would really have been retrieved for its question. Not one
   * of them may be flagged: an over-eager checker is worse than none, because a
   * reviewer who sees it cry wolf once stops reading the badge.
   */
  const legitimate: Array<[string, string]> = [
    [
      "What did the pipeline do about TX-03?",
      `TX-03 is the silent-discount class. The audit records ${facts.detected["TX-03"]} rows whose ` +
        `reported total_amount is below quantity × unit_price. The reported total is preserved, ` +
        `and the difference is exposed as discount_amount: ${facts.recon.discountTotal} in total. ` +
        `Net revenue is ${facts.recon.netRevenue}.`,
    ],
    [
      "Summarise the revenue reconciliation.",
      `Gross list value is ${facts.recon.grossListValue}, discounts are ${facts.recon.discountTotal}, ` +
        `gross sales net of discount is ${facts.recon.grossSalesNetOfDiscount}, returns are ` +
        `${facts.recon.returnsValue} and net revenue is ${facts.recon.netRevenue}. Both published ` +
        `deltas are $0.00.`,
    ],
    [
      "How many rows survived cleaning?",
      `The run read ${facts.raw.transactions} transaction rows and loaded ${facts.cleaned.transactions} ` +
        `into fact_sales, with ${facts.quarantined} rows quarantined. dim_customer holds ` +
        `${facts.warehouse.dim_customer} members and fk_violations is 0.`,
    ],
    [
      "What is the defect coverage?",
      `Coverage is ${facts.coverage.matched} matched of ${facts.coverage.expected} expected classes, ` +
        `with ${facts.coverage.detected} detected. There are no count mismatches.`,
    ],
    [
      "which stores exceed the return rate threshold?",
      "S006 Lakeside Shopping Ctr shows a 13.73% unit return rate against a 12.50% transaction-based " +
        "rate, on 88 units sold and 14 returned. S015 Alderwood Mall is 13.51% and 15.38%.",
    ],
    [
      "what is the average order value by region?",
      "Northeast averages $389.05 across 165 transactions totalling $64,192.45. South averages " +
        "$384.49 across 83 transactions.",
    ],
    [
      "how did you handle orphaned store ids?",
      "TX-04 covers transactions whose store_id has no match in dim_store. The audit records " +
        `${facts.detected["TX-04"]} such rows, and they are quarantined rather than loaded against ` +
        "an Unknown member, so fk_violations stays at 0.",
    ],
    [
      "what happened in June?",
      "June 2026 carries a single day of data, 2026-06-01, so the month-over-month figure for it is " +
        "an artefact of where the extract was cut. The metric emits days_with_data on every row.",
    ],
    [
      "Do you have the test results?",
      "The context does not include test results — no test counts, pass rates or mutation-testing " +
        "figures are in the retrieved bundle excerpts. I would need the test report to answer that.",
    ],
  ];

  let falsePositives = 0;
  for (const [question, answer] of legitimate) {
    const result = auditAgainstContext(answer, selectContext(bundle, question).text);
    const flagged = result.verdict === "warn";
    if (flagged) falsePositives += 1;
    check(
      `legitimate answer is not flagged: "${question}"`,
      !flagged,
      flagged ? result.figures.filter((f) => f.verdict === "unverified").map((f) => `${f.text} (${f.excerpt})`).join(" | ") : "",
    );
  }
  console.log(
    `  … false-positive rate on legitimate answers: ${falsePositives}/${legitimate.length}`,
  );

  /* ── 10. Verifier applied to the scripted answers ───────────────────────── */

  section("numeric self-audit: scripted answers");

  const bundleIndex = indexBundleNumbers(bundle);
  const scriptedAudits = answers.map((a) => ({
    label: a.label,
    result: auditAnswer([a.answer, ...a.talkingPoints].join("\n"), bundleIndex),
  }));
  const scriptedWarnings = scriptedAudits.filter((s) => s.result.verdict === "warn");
  check(
    "every scripted answer passes its own audit (they are generated from the bundle)",
    scriptedWarnings.length === 0,
    scriptedWarnings
      .map(
        (s) =>
          `${s.label}: ${s.result.figures
            .filter((f) => f.verdict === "unverified")
            .map((f) => f.text)
            .join(", ")}`,
      )
      .join(" | "),
  );
  check(
    "the scripted audits actually checked figures rather than exempting everything",
    scriptedAudits.reduce((n, s) => n + s.result.checked, 0) > 50,
    String(scriptedAudits.reduce((n, s) => n + s.result.checked, 0)),
  );
  check(
    "a scripted audit says it was checked against the bundle, not a retrieved slice",
    scriptedAudits[0].result.source === "bundle",
  );

  /* ── 11. The audit on the wire ──────────────────────────────────────────── */

  section("route handler: numeric audit in the response");

  const auditOkRes = await handleChatPost(
    post({ question: "What is the net revenue?" }),
    makeDeps({ respond: () => geminiOk(`Net revenue is ${facts.recon.netRevenue}.`) }),
  );
  const auditOkBody = (await auditOkRes.json()) as ChatResponse;
  check(
    "a grounded answer comes back with a passing audit",
    auditOkBody.ok === true && auditOkBody.audit?.verdict === "pass",
    JSON.stringify(auditOkBody.ok === true ? auditOkBody.audit : null),
  );
  check(
    "the audit says what it was checked against",
    auditOkBody.ok === true && auditOkBody.audit?.source === "retrieved-context",
  );
  check(
    "the audit carries its own limitation statement",
    auditOkBody.ok === true && (auditOkBody.audit?.limitation ?? "").includes("does not prove"),
  );

  const auditBadRes = await handleChatPost(
    post({ question: "What is the net revenue?" }),
    makeDeps({ respond: () => geminiOk("Net revenue is $174,213.66 across 691 fact rows.") }),
  );
  const auditBadBody = (await auditBadRes.json()) as ChatResponse;
  check(
    "an answer containing invented figures is flagged on the wire",
    auditBadBody.ok === true && auditBadBody.audit?.verdict === "warn",
  );
  check(
    "the invented figures are named for the client to render",
    auditBadBody.ok === true &&
      (auditBadBody.audit?.figures ?? []).some((f) => f.text.includes("174,213.66")),
  );
  check(
    "a flagged answer is still returned rather than suppressed",
    auditBadBody.ok === true && auditBadBody.answer.includes("174,213.66"),
  );
  check(
    "the alias phrases that fired are reported to the client",
    auditBadBody.ok === true && Array.isArray(auditBadBody.context.aliasPhrases),
  );

  /* ── Result ─────────────────────────────────────────────────────────────── */

  console.log(`\n${passed} passed, ${failures.length} failed`);
  if (failures.length) {
    for (const f of failures) console.log(`  - ${f}`);
    process.exitCode = 1;
  }
}

void main();
