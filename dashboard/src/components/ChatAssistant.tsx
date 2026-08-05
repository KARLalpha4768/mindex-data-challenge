"use client";

/**
 * Reviewer's assistant panel.
 *
 * WHAT CHANGED, AND WHY IT MATTERS
 * --------------------------------
 * This component used to be an "AI assistant" that contained no AI: it
 * substring-matched a hand-written array of answers and rendered the winner.
 * That is a defensible thing to build — it is fast, free and offline — but it
 * was not labelled as such, and its hand-written answers had gone numerically
 * stale ($170,816.34 for a figure that is now $168,957.80). A reviewer reading
 * it was being told two untrue things at once.
 *
 * It now has two modes, and it always says which one produced the text on
 * screen:
 *
 *   LIVE     `POST /api/chat` → Gemini, grounded on a retrieved slice of the
 *            bundle by `src/lib/grounding.ts`. Available only when the
 *            deployment has `GEMINI_API_KEY` set.
 *   OFFLINE  Scripted answers generated from the bundle at render time by
 *            `src/lib/presets.ts`. Used when there is no key, when the API call
 *            fails, and for every preset-chip click.
 *
 * Every assistant message carries a source badge. "Gemini" and "Scripted" are
 * visually distinct and the scripted ones say so in words. There is no state in
 * which a reviewer can mistake one for the other — which is the entire point,
 * because a submission arguing for numerical trustworthiness cannot afford an
 * unmarked machine-generated dollar figure.
 *
 * Next to that badge sits the NUMERIC SELF-AUDIT (`numericAudit.ts`): every
 * figure in the message, looked up in the material the message was grounded on.
 * Live answers carry the server's audit against the retrieved context; scripted
 * answers are audited here in the browser against the whole bundle, using the
 * same code, so the badge means the same thing in both modes and the scripted
 * set acts as a standing sanity check on the verifier.
 *
 * WHY THE PANEL NOW TALKS ABOUT MODELS
 * ------------------------------------
 * The server chooses its model from a preference chain filtered by what the API
 * key's own project reports (see `chatHandler.ts`). Two consequences reach the
 * UI, and both are deliberate:
 *
 *   • the badge and the provenance line name the model that ACTUALLY answered,
 *     which is not always the first choice — and when earlier candidates were
 *     skipped, the message says so. A silent fallback looks identical to a
 *     first-choice success, and the difference is exactly what someone
 *     debugging a deployment needs;
 *   • a rejected API key (HTTP 401/403) gets its own banner rather than being
 *     folded into the generic "offline mode" copy, which would be actively
 *     misleading: a key IS configured, and Google refused it. That banner
 *     carries the server's remedy verbatim, so the answer to "is this my key or
 *     their code?" is on screen without opening a network tab.
 *
 * The panel also opens on the ten ranked interview questions rather than on an
 * empty text box, because a reviewer who has to guess what the assistant can
 * answer will ask it something it cannot, once, and then stop.
 *
 * NOTHING SECRET REACHES THIS FILE. The browser bundle contains no key and no
 * upstream URL; it knows only the boolean returned by `GET /api/chat`.
 */

import React from "react";

import { Badge, CopyButton } from "@/components/ui";
import {
  MAX_HISTORY_TURNS,
  MAX_QUESTION_CHARS,
  type ChatErrorKind,
  type ChatResponse,
  type ChatStatusResponse,
  type ModelResolution,
  type NumericAudit,
} from "@/lib/chatContract";
import { auditAnswer, indexBundleNumbers } from "@/lib/numericAudit";
import {
  INTERVIEW_QUESTIONS,
  buildScriptedAnswers,
  findScriptedAnswer,
  resolveInterviewAnswer,
  type CodeAnnotation,
  type InterviewQuestion,
  type ScriptedAnswer,
} from "@/lib/presets";
import type { Bundle, DefectView } from "@/lib/types";

/** Where a message's text came from. Drives the badge; never inferred later. */
type MessageSource = "user" | "gemini" | "scripted" | "system" | "pending";

interface Message {
  id: string;
  source: MessageSource;
  text: string;
  /** Honest one-line provenance, e.g. "offline mode — scripted answer". */
  sourceNote?: string;
  defectCode?: string;
  codeRef?: string;
  codeSnippet?: string;
  codeAnnotations?: CodeAnnotation[];
  talkingPoints?: string[];
  /** What the server retrieved, surfaced so retrieval is inspectable. */
  contextNote?: string;
  /**
   * Result of the numeric self-audit for this message. Live answers carry the
   * server's audit (checked against the retrieved context); scripted answers
   * are audited here in the browser against the whole bundle. Same code both
   * sides — see `numericAudit.ts`.
   */
  audit?: NumericAudit;
  timestamp: string;
  isError?: boolean;
}

interface Props {
  bundle: Bundle;
  defects: DefectView[];
  onSelectDefect?: (code: string) => void;
}

/** "checking" until `GET /api/chat` answers; then live or offline for the session. */
type Mode = "checking" | "live" | "offline";

const DEFECT_CODE_RE = /\b(?:ST|PR|TX)-\d{2}\b/i;

function now(): string {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Human copy per failure kind. The client must never render `kind` raw. */
const ERROR_COPY: Record<ChatErrorKind, string> = {
  not_configured: "no GEMINI_API_KEY on this deployment",
  rate_limited: "rate limit reached",
  bad_request: "the request was rejected",
  too_large: "the question was too long",
  bundle_unavailable: "the server could not read the data bundle",
  upstream_error: "the model API call failed",
  /**
   * Deliberately worded to end the "is it me or the code?" question in one
   * reading. Every other entry describes a symptom; this one names the cause,
   * because it is the only failure class where the cause is knowable from the
   * status alone and the fix is not in this repository.
   */
  upstream_auth: "the API key was REJECTED by Google — this is a key problem, not a code problem",
  blocked: "the model declined to answer",
  timeout: "the model timed out",
  empty_response: "the model returned nothing",
};

/**
 * The numeric self-audit, rendered next to the source badge.
 *
 * Calm and factual on purpose. A passing state says what was checked, not
 * "VERIFIED ✓"; a warning state names the figures and does not editorialise
 * about them, because an unverified figure is not proof of a wrong figure — it
 * is proof that this check could not confirm it, and those are different
 * claims. Both states carry the limitation string verbatim, so the badge never
 * asserts more than the check performs.
 */
function AuditNote({ audit }: { audit: NumericAudit }) {
  const where =
    audit.source === "retrieved-context" ? "the retrieved context" : "bundle.json";

  if (audit.verdict === "no-figures") {
    return (
      <p className="text-2xs text-ink-faint">
        <Badge tone="neutral">no figures</Badge>{" "}
        <span className="italic">This answer states no checkable figure.</span>
      </p>
    );
  }

  const unverified = audit.figures.filter((f) => f.verdict === "unverified");

  return (
    <details className="rounded border border-line/60 bg-panel/60 p-2.5">
      <summary className="cursor-pointer text-2xs">
        {audit.verdict === "pass" ? (
          <>
            <Badge tone="ok" title="Post-response numeric check">
              figures checked
            </Badge>{" "}
            <span className="text-ink-dim">
              every figure in this answer appears in {where}
              {audit.derived > 0
                ? ` (${audit.derived} of ${audit.checked} as arithmetic over figures shown above)`
                : ""}
            </span>
          </>
        ) : (
          <>
            <Badge tone="warn" title="Post-response numeric check">
              {unverified.length} figure{unverified.length === 1 ? "" : "s"} unverified
            </Badge>{" "}
            <span className="text-ink-dim">
              not found in {where}: {unverified.map((f) => f.text).join(", ")}
            </span>
          </>
        )}
      </summary>

      <div className="mt-2 space-y-2 text-2xs leading-relaxed text-ink-dim">
        <p className="font-mono">
          {audit.checked} checked · {audit.verified} present · {audit.derived} computed ·{" "}
          {audit.unverified} not found · {audit.exemptCount} not applicable
          {Object.keys(audit.exemptByKind).length > 0 && (
            <>
              {" "}
              (
              {Object.entries(audit.exemptByKind)
                .map(([kind, n]) => `${n} ${kind}`)
                .join(", ")}
              )
            </>
          )}
        </p>

        {unverified.length > 0 && (
          <ul className="list-inside list-disc space-y-1">
            {unverified.map((f, i) => (
              <li key={i}>
                <span className="font-mono text-warn">{f.text}</span> — {f.excerpt}
              </li>
            ))}
          </ul>
        )}

        <p className="italic">{audit.limitation}</p>
        {audit.truncated && <p className="italic">Figure list truncated for payload size.</p>}
      </div>
    </details>
  );
}

export default function ChatAssistant({ bundle, defects, onSelectDefect }: Props) {
  const [isOpen, setIsOpen] = React.useState(false);
  const [inputQuery, setInputQuery] = React.useState("");
  const [mode, setMode] = React.useState<Mode>("checking");
  const [modelName, setModelName] = React.useState<string>("");
  const [isSending, setIsSending] = React.useState(false);

  /**
   * The one failure the reviewer must not have to diagnose.
   *
   * A rejected key (HTTP 401/403 from Google) is the single class of failure
   * that no amount of retrying, model-switching or redeploying fixes. It gets
   * its own banner, in its own colour, carrying the server's `remedy` string
   * verbatim — so the answer to "is this broken because of my key or because of
   * their code?" is on screen without opening a network tab. Null in every
   * other state, including every other kind of failure.
   */
  const [keyRejected, setKeyRejected] = React.useState<{ message: string; remedy?: string } | null>(
    null,
  );

  /**
   * What the server said about model selection on the last exchange. Purely a
   * diagnosis surface: which names were queued, which were skipped as
   * unavailable to this key's project, and which one answered.
   */
  const [resolution, setResolution] = React.useState<ModelResolution | null>(null);

  /**
   * Scripted answers are derived from the bundle, so they are recomputed only
   * when the bundle identity changes — i.e. never, in practice. Doing it in a
   * memo rather than at module scope keeps the derivation honest: there is no
   * cached copy of the numbers anywhere that could outlive the data.
   */
  const scripted = React.useMemo(() => buildScriptedAnswers(bundle), [bundle]);

  /**
   * Numeric index of the whole bundle, for auditing scripted answers.
   *
   * Scripted answers are assembled from all of `bundle.json`, not from a
   * retrieved slice, so the honest provenance claim for them is "this figure is
   * in the bundle" and that is what the badge says. Built once per bundle
   * identity — i.e. once — because it walks a megabyte of JSON.
   */
  const bundleNumbers = React.useMemo(() => indexBundleNumbers(bundle), [bundle]);

  /** Defect codes that actually exist, so a deep link can never point nowhere. */
  const knownCodes = React.useMemo(
    () => new Set(defects.map((d) => d.code.toUpperCase())),
    [defects],
  );

  const [messages, setMessages] = React.useState<Message[]>(() => [
    {
      id: "init",
      source: "system",
      text:
        "Ask about the pipeline: any of the 17 defect classes, the cleaning decision taken on " +
        "each and why, the star schema, or any of the SQL metrics.\n\n" +
        "Answers are grounded: the server retrieves the relevant slice of the pipeline's own " +
        "output bundle and the model is instructed to answer from that and nothing else. If the " +
        "context does not contain an answer it will say so rather than invent one.\n\n" +
        "Every answer is then checked: each figure in it is looked up in the material it was " +
        "grounded on, and the result is shown under the answer. That check proves a figure is " +
        "present — not that it was used correctly.\n\n" +
        "Start with the ranked questions above. The collapsed bundle chips return scripted " +
        "answers assembled directly from bundle.json — no model involved, every figure read at " +
        "render time.",
      timestamp: now(),
    },
  ]);

  const messagesEndRef = React.useRef<HTMLDivElement>(null);
  const abortRef = React.useRef<AbortController | null>(null);

  React.useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  /**
   * Capability probe, once, on first open.
   *
   * Deferred until the panel is opened so a reviewer who never touches the
   * assistant costs the deployment zero function invocations. On failure we
   * fall to offline mode rather than retrying: a static-hosted copy of this app
   * has no `/api/chat` at all and would 404 forever.
   */
  React.useEffect(() => {
    if (!isOpen || mode !== "checking") return;
    let cancelled = false;

    (async () => {
      try {
        const res = await fetch("/api/chat", { method: "GET" });
        if (!res.ok) throw new Error(`status ${res.status}`);
        const status = (await res.json()) as ChatStatusResponse;
        if (cancelled) return;
        setModelName(status.model ?? "");
        // Additive fields: a server built against the earlier contract omits
        // them and the panel simply has nothing extra to show.
        if (status.candidates?.length) {
          setResolution({
            preference: status.preference ?? status.candidates,
            candidates: status.candidates,
            ...(status.resolvedModel ? { selected: status.resolvedModel } : {}),
            ...(status.modelOverride ? { requested: status.modelOverride } : {}),
            discovery: status.discovery ?? "not-attempted",
            discoveryNote: status.discoveryNote ?? "",
            attempts: [],
            skipped: (status.retired ?? []).map((r) => r.model),
            unavailable: [],
          });
        }
        setMode(status.configured && status.bundleAvailable ? "live" : "offline");
      } catch {
        if (!cancelled) setMode("offline");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [isOpen, mode]);

  // Abandon an in-flight request if the panel closes or unmounts. Without this
  // a slow answer lands in a panel nobody is looking at and the button stays
  // disabled behind it.
  React.useEffect(() => {
    if (!isOpen && abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
      setIsSending(false);
      setMessages((prev) => prev.filter((m) => m.source !== "pending"));
    }
  }, [isOpen]);

  React.useEffect(() => () => abortRef.current?.abort(), []);

  const pushScripted = React.useCallback(
    (answer: ScriptedAnswer, note: string, isError = false) => {
      // The same verifier the server runs on model output, run here on the
      // scripted text. These answers are generated from the bundle, so this
      // should pass trivially — which makes it a standing sanity check on the
      // verifier itself: a warning on a scripted answer means either a
      // hand-typed figure crept back into `presets.ts` or the checker is wrong.
      const audit = auditAnswer(
        [answer.answer, ...(answer.talkingPoints ?? [])].join("\n"),
        bundleNumbers,
      );

      setMessages((prev) => [
        ...prev.filter((m) => m.source !== "pending"),
        {
          id: `s-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          source: "scripted",
          text: answer.answer,
          sourceNote: note,
          defectCode: answer.defectCode || undefined,
          codeRef: answer.codeRef || undefined,
          codeSnippet: answer.codeSnippet || undefined,
          codeAnnotations: answer.codeAnnotations?.length ? answer.codeAnnotations : undefined,
          talkingPoints: answer.talkingPoints,
          audit,
          timestamp: now(),
          isError,
        },
      ]);
    },
    [bundleNumbers],
  );

  /** Bundle chips are always scripted — deterministic, free, and labelled as such. */
  const handlePresetSelect = (preset: ScriptedAnswer) => {
    setMessages((prev) => [
      ...prev,
      {
        id: `u-${Date.now()}`,
        source: "user",
        text: preset.question,
        timestamp: now(),
      },
    ]);
    pushScripted(preset, "scripted answer — assembled from bundle.json, no model call");
  };

  /**
   * Replay the recent turns so follow-up questions ("and what about the other
   * one?") resolve. Capped client-side at the same limit the server enforces,
   * so the trimming is visible here rather than being a silent server-side
   * truncation. Scripted answers are replayed too — they are honest content and
   * dropping them would make the transcript incoherent to the model.
   */
  const buildHistory = React.useCallback(() => {
    return messages
      .filter((m) => m.source === "user" || m.source === "gemini" || m.source === "scripted")
      .slice(-MAX_HISTORY_TURNS)
      .map((m) => ({ role: m.source === "user" ? ("user" as const) : ("model" as const), text: m.text }));
  }, [messages]);

  /**
   * Ask one question through the normal path.
   *
   * Extracted from the form handler so the ten interview chips can use exactly
   * the same route a typed question does — live model when one is configured,
   * scripted answer when not. A chip that behaved differently from typing its
   * text would be a demo, not a feature.
   *
   * `offlineAnswer` lets a caller pin which scripted answer the offline path
   * should use. The interview chips do that: free-text matching cannot connect
   * "June 2026 shows a 98% revenue collapse" to a metric whose label shares no
   * word with it.
   */
  const askQuestion = async (question: string, offlineAnswer?: ScriptedAnswer) => {
    if (!question || isSending) return;
    const fallback = () => offlineAnswer ?? findScriptedAnswer(scripted, question);

    const history = buildHistory();

    setMessages((prev) => [
      ...prev,
      { id: `u-${Date.now()}`, source: "user", text: question, timestamp: now() },
    ]);

    // Offline: answer immediately from the bundle. No spinner, no round trip.
    if (mode !== "live") {
      pushScripted(
        fallback(),
        mode === "checking"
          ? "offline mode — scripted answer (still checking whether a live model is configured)"
          : "offline mode — scripted answer (no live model configured on this deployment)",
      );
      return;
    }

    setIsSending(true);
    setMessages((prev) => [
      ...prev,
      {
        id: `p-${Date.now()}`,
        source: "pending",
        text: "Retrieving the relevant slice of the bundle and asking the model…",
        timestamp: now(),
      },
    ]);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: question.slice(0, MAX_QUESTION_CHARS), history }),
        signal: controller.signal,
      });

      const data = (await res.json()) as ChatResponse;

      if (data.ok) {
        const code = data.context.mentionedCodes.find((c) => knownCodes.has(c.toUpperCase()));
        const inferred = data.answer.match(DEFECT_CODE_RE)?.[0]?.toUpperCase();
        const linkCode = code ?? (inferred && knownCodes.has(inferred) ? inferred : undefined);

        // The live model answered, so any earlier key complaint is stale.
        setKeyRejected(null);
        if (data.resolution) setResolution(data.resolution);
        // `data.model` is the model that ACTUALLY produced this text, which is
        // not necessarily the first choice. Show it, not the configured name.
        setModelName(data.model);

        // When the chain skipped a candidate, say so on the message itself.
        // A silent fallback is indistinguishable from a first-choice success,
        // and the difference is exactly what a reviewer would want to know.
        const skipped = data.resolution?.skipped ?? [];
        const skipNote = skipped.length
          ? ` · ${skipped.length} earlier candidate${skipped.length === 1 ? "" : "s"} skipped as ` +
            `unavailable to this API key: ${skipped.join(", ")}`
          : "";

        setMessages((prev) => [
          ...prev.filter((m) => m.source !== "pending"),
          {
            id: `g-${Date.now()}`,
            source: "gemini",
            text: data.answer,
            sourceNote: `generated by ${data.model}, grounded on retrieved bundle context${skipNote}`,
            defectCode: linkCode,
            contextNote:
              `context: ~${data.context.approxTokens.toLocaleString()} of ` +
              `${data.context.budgetTokens.toLocaleString()} token budget · ` +
              `${data.context.includedIds.join(", ")}` +
              (data.context.droppedIds.length
                ? ` · dropped for budget: ${data.context.droppedIds.join(", ")}`
                : "") +
              (data.context.aliasPhrases?.length
                ? ` · alias phrases matched: ${data.context.aliasPhrases.join(", ")}`
                : ""),
            // Optional on the wire: a server built against the earlier contract
            // simply omits it, and the message renders without the badge.
            audit: data.audit,
            timestamp: now(),
          },
        ]);
      } else {
        // Graceful degradation: the reviewer still gets a real, bundle-derived
        // answer, and is told exactly why it is not the live one.
        if (data.resolution) setResolution(data.resolution);
        if (data.kind === "not_configured" || data.kind === "bundle_unavailable") setMode("offline");
        /**
         * A rejected key is terminal for the session. Dropping to offline mode
         * means the next nine questions answer instantly from the bundle
         * instead of each spending a round-trip to be told the same thing, and
         * the banner explains why rather than leaving the panel to look
         * mysteriously downgraded.
         */
        if (data.kind === "upstream_auth") {
          setKeyRejected({ message: data.message, remedy: data.remedy });
          setMode("offline");
        }
        // The server computes a precise reason — e.g. "Model API returned HTTP
        // 404 — the model name may not exist for this API version". Discarding
        // it in favour of the generic category left "the model API call failed"
        // as the only signal, which is not enough to act on. The server's
        // message is already redacted of the API key on every path.
        const detail = typeof data.message === "string" && data.message ? ` · ${data.message}` : "";
        pushScripted(
          fallback(),
          `offline mode — scripted answer (${ERROR_COPY[data.kind] ?? "the live model was unavailable"}${detail})`,
          true,
        );
      }
    } catch (err) {
      if ((err as { name?: string })?.name === "AbortError") return;
      pushScripted(
        fallback(),
        "offline mode — scripted answer (the request to /api/chat failed)",
        true,
      );
    } finally {
      abortRef.current = null;
      setIsSending(false);
    }
  };

  const handleSend = (e: React.FormEvent) => {
    e.preventDefault();
    const question = inputQuery.trim();
    if (!question || isSending) return;
    setInputQuery("");
    void askQuestion(question);
  };

  /** An interview chip asks its question through the same path as typing it. */
  const handleInterviewSelect = (item: InterviewQuestion) => {
    if (isSending) return;
    void askQuestion(item.question, resolveInterviewAnswer(scripted, item));
  };

  /** Deep link into the Defect Explorer. Only fires for codes that exist. */
  const handleNavigateToCode = (m: Message) => {
    if (!onSelectDefect) return;
    const fromMessage = m.defectCode?.toUpperCase();
    const fromRef = m.codeRef?.match(DEFECT_CODE_RE)?.[0]?.toUpperCase();
    const code = [fromMessage, fromRef].find((c) => c && knownCodes.has(c));
    if (!code) return;
    onSelectDefect(code);
    setIsOpen(false);
  };

  const canNavigate = (m: Message) => {
    const fromMessage = m.defectCode?.toUpperCase();
    const fromRef = m.codeRef?.match(DEFECT_CODE_RE)?.[0]?.toUpperCase();
    return Boolean(onSelectDefect && [fromMessage, fromRef].some((c) => c && knownCodes.has(c)));
  };

  const modeBadge =
    keyRejected ? (
      // Distinct from every other offline state on purpose: "offline · scripted"
      // reads as a configuration choice, and this is not one.
      <Badge tone="bad" title="Google rejected the configured API key (HTTP 401/403)">
        key rejected · scripted
      </Badge>
    ) : mode === "live" ? (
      <Badge tone="ok" title={`Live answers from ${modelName || "the configured model"}`}>
        live · {modelName || "model"}
      </Badge>
    ) : mode === "offline" ? (
      <Badge tone="warn" title="No live model configured; answers are assembled from bundle.json">
        offline · scripted
      </Badge>
    ) : (
      <Badge tone="neutral">checking…</Badge>
    );

  const handleExportTranscript = () => {
    const lines: string[] = [
      "# Karl David's Solution — Reviewer Assistant Q&A Transcript",
      `*Generated at ${new Date().toLocaleString()}*`,
      "",
      "---",
      "",
    ];
    for (const m of messages) {
      if (m.source === "system") continue;
      lines.push(`### **[${m.source.toUpperCase()}]** (${m.timestamp})`);
      lines.push(m.text);
      if (m.talkingPoints?.length) {
        lines.push("\n**Talking Points:**");
        for (const tp of m.talkingPoints) lines.push(`- ${tp}`);
      }
      if (m.codeRef) lines.push(`\n*Code Reference:* \`${m.codeRef}\``);
      lines.push("\n---\n");
    }
    const blob = new Blob([lines.join("\n")], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `mindex_reviewer_qa_transcript_${Date.now()}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <>
      {/* Floating toggle */}
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="fixed bottom-5 right-5 z-50 flex items-center gap-2 rounded-full border border-accent bg-accent/10 px-4 py-2.5 font-medium text-accent shadow-lg backdrop-blur transition-all hover:bg-accent/20"
        aria-expanded={isOpen}
        title="Open the grounded reviewer's assistant"
      >
        <span className="text-sm font-semibold">Ask about this pipeline</span>
        <span className="flex h-2 w-2 rounded-full bg-ok" aria-hidden="true" />
      </button>

      {isOpen && (
        <div
          className="fixed inset-y-0 right-0 z-50 flex w-full max-w-2xl flex-col border-l border-line bg-panel shadow-2xl"
          role="dialog"
          aria-label="Reviewer's assistant"
        >
          <header className="flex items-start justify-between gap-3 border-b border-line bg-raised px-5 py-3.5">
            <div>
              <h2 className="text-base font-semibold text-ink">Grounded reviewer&apos;s assistant</h2>
              <p className="mt-0.5 flex items-center gap-2 text-2xs text-ink-dim">
                {modeBadge}
                <span>
                  {mode === "live"
                    ? "answers retrieved from bundle.json, generated by the model"
                    : "answers assembled directly from bundle.json"}
                </span>
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleExportTranscript}
                className="rounded border border-line bg-panel px-2.5 py-1 text-xs text-accent hover:border-accent hover:bg-accent/10"
                title="Download transcript of current Q&A session as Markdown"
              >
                Export Q&A Log
              </button>
              <button
                type="button"
                onClick={() => setIsOpen(false)}
                className="rounded px-2.5 py-1 text-xs text-ink-dim hover:bg-panel hover:text-ink"
              >
                Close
              </button>
            </div>
          </header>

          {/* The rejected-key banner. Takes the place of the generic offline
              explainer, because when the key is the problem, "no key is
              configured (or the bundle is unreadable)" is actively misleading:
              a key IS configured, and Google refused it. */}
          {keyRejected && (
            <div className="border-b border-bad/50 bg-bad/10 px-5 py-3 text-2xs leading-relaxed text-ink">
              <p className="text-xs font-semibold text-bad">
                The API key was rejected by Google. This is not a bug in this app.
              </p>
              <p className="mt-1.5 text-ink-dim">{keyRejected.message}</p>
              {keyRejected.remedy && (
                <p className="mt-1.5 text-ink-dim">
                  <span className="font-semibold text-ink">How to fix it: </span>
                  {keyRejected.remedy}
                </p>
              )}
              <p className="mt-1.5 text-ink-faint">
                Until then the panel answers from{" "}
                <code className="font-mono">bundle.json</code>, which needs no key and no network.
              </p>
            </div>
          )}

          {/* Model-selection diagnosis. Collapsed, because on a healthy
              deployment there is nothing here worth the vertical space — and
              on a broken one it is the first thing anyone will want. */}
          {resolution && (
            <details className="border-b border-line bg-panel/40 px-5 py-2">
              <summary className="cursor-pointer text-2xs text-ink-faint hover:text-accent">
                model selection —{" "}
                {resolution.selected
                  ? `answering with ${resolution.selected}`
                  : `${resolution.candidates.length} candidate${resolution.candidates.length === 1 ? "" : "s"} queued`}
                {resolution.skipped.length > 0 && `, ${resolution.skipped.length} skipped`}
              </summary>
              <div className="mt-2 space-y-1 font-mono text-2xs leading-relaxed text-ink-dim">
                {resolution.requested && <p>GEMINI_MODEL override: {resolution.requested}</p>}
                <p>preference: {resolution.preference.join(" → ")}</p>
                <p>tried in order: {resolution.candidates.join(" → ")}</p>
                <p>
                  discovery ({resolution.discovery}): {resolution.discoveryNote}
                </p>
                {resolution.unavailable.length > 0 && (
                  <p>not reported by ListModels for this key: {resolution.unavailable.join(", ")}</p>
                )}
                {resolution.attempts.length > 0 && (
                  <ul className="list-inside list-disc">
                    {resolution.attempts.map((a, i) => (
                      <li key={i}>
                        {a.model} — {a.outcome}
                        {a.status !== undefined ? ` (HTTP ${a.status})` : ""} after {a.attempts}{" "}
                        attempt{a.attempts === 1 ? "" : "s"}: {a.reason}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </details>
          )}

          {/* Offline explainer — say why, not just that. */}
          {mode === "offline" && !keyRejected && (
            <div className="border-b border-line bg-warn/5 px-5 py-2.5 text-2xs leading-relaxed text-ink-dim">
              <span className="font-semibold text-warn">Offline mode.</span> This deployment has no{" "}
              <code className="font-mono">GEMINI_API_KEY</code> configured (or the server could not read
              the bundle), so no model is being called. Every answer below is assembled from{" "}
              <code className="font-mono">bundle.json</code> — the pipeline&apos;s own output — and every
              figure in it is read at render time.
            </div>
          )}

          {/* The ten interview questions, ranked. Four visible, six behind a
              disclosure: ten chips at once would push the transcript off the
              screen, which is the thing a reviewer actually came to read. */}
          <div className="border-b border-line bg-panel/50 p-4">
            <p className="mb-2 text-2xs font-medium uppercase tracking-wider text-ink-faint">
              The ten hardest questions about this pipeline, ranked
            </p>
            <div className="flex flex-wrap gap-1.5">
              {INTERVIEW_QUESTIONS.slice(0, 4).map((item) => (
                <button
                  key={item.rank}
                  type="button"
                  disabled={isSending}
                  onClick={() => handleInterviewSelect(item)}
                  title={item.question}
                  className="rounded border border-accent/40 bg-accent/5 px-2.5 py-1 text-xs text-ink-dim transition-colors hover:border-accent hover:text-accent disabled:opacity-50"
                >
                  <span className="mr-1.5 font-mono text-2xs text-accent">{item.rank}</span>
                  {item.chip}
                </button>
              ))}
            </div>

            <details className="mt-2">
              <summary className="cursor-pointer text-2xs text-ink-faint hover:text-accent">
                six more ({INTERVIEW_QUESTIONS.length - 4})
              </summary>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {INTERVIEW_QUESTIONS.slice(4).map((item) => (
                  <button
                    key={item.rank}
                    type="button"
                    disabled={isSending}
                    onClick={() => handleInterviewSelect(item)}
                    title={item.question}
                    className="rounded border border-accent/40 bg-accent/5 px-2.5 py-1 text-xs text-ink-dim transition-colors hover:border-accent hover:text-accent disabled:opacity-50"
                  >
                    <span className="mr-1.5 font-mono text-2xs text-accent">{item.rank}</span>
                    {item.chip}
                  </button>
                ))}
              </div>
            </details>

            <p className="mt-2 text-2xs text-ink-faint">
              These are asked through the normal path: the live model when one is configured,
              the scripted bundle answer when not.
            </p>

            {/* Bundle chips: one per defect class, one per metric, plus the run
                summary. Collapsed by default — twenty-four of them above the
                transcript was the old layout, and it buried everything else. */}
            <details className="mt-3 border-t border-line/60 pt-2.5">
              <summary className="cursor-pointer text-2xs font-medium uppercase tracking-wider text-ink-faint hover:text-accent">
                or jump straight to a scripted answer ({scripted.length})
              </summary>
              <div className="mt-2 flex max-h-32 flex-wrap gap-1.5 overflow-y-auto">
                {scripted.map((p) => (
                  <button
                    key={p.label}
                    type="button"
                    onClick={() => handlePresetSelect(p)}
                    className="rounded border border-line bg-raised px-2.5 py-1 font-mono text-xs text-ink-dim transition-colors hover:border-accent hover:text-accent"
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </details>
          </div>

          {/* Transcript */}
          <div className="flex-1 space-y-6 overflow-y-auto p-5 text-xs">
            {messages.map((m) => (
              <div
                key={m.id}
                className={`flex flex-col ${m.source === "user" ? "items-end" : "items-start"}`}
              >
                <div className="mb-1.5 flex items-center gap-2 text-2xs text-ink-faint">
                  <span>
                    {m.source === "user"
                      ? "You"
                      : m.source === "gemini"
                        ? "Assistant"
                        : m.source === "pending"
                          ? "Assistant"
                          : "Assistant"}
                  </span>
                  <span aria-hidden="true">·</span>
                  <span>{m.timestamp}</span>
                  {m.source === "gemini" && <Badge tone="accent">model</Badge>}
                  {m.source === "scripted" && (
                    <Badge tone={m.isError ? "warn" : "neutral"}>scripted</Badge>
                  )}
                </div>

                <div
                  className={`max-w-[95%] space-y-4 rounded-lg p-4 ${
                    m.source === "user"
                      ? "border border-accent/20 bg-accent/10 text-ink"
                      : "border border-line bg-raised text-ink"
                  }`}
                >
                  {/* Provenance line. Always present for assistant output. */}
                  {m.sourceNote && (
                    <p className="text-2xs italic text-ink-faint">{m.sourceNote}</p>
                  )}

                  {/* Numeric self-audit, immediately under the provenance line:
                      where the text came from, and whether its figures are in
                      the material it came from, are the same question. */}
                  {m.audit && <AuditNote audit={m.audit} />}

                  {m.source === "pending" ? (
                    <p className="flex items-center gap-2 text-sm text-ink-dim">
                      <span
                        className="h-2 w-2 animate-pulse rounded-full bg-accent"
                        aria-hidden="true"
                      />
                      {m.text}
                    </p>
                  ) : (
                    <div className="whitespace-pre-wrap text-sm leading-relaxed">{m.text}</div>
                  )}

                  {m.talkingPoints && m.talkingPoints.length > 0 && (
                    <div className="space-y-2 rounded border border-line/60 bg-panel/60 p-3">
                      <p className="text-2xs font-semibold uppercase tracking-wider text-accent">
                        From the audit ledger
                      </p>
                      <ul className="list-inside list-disc space-y-1.5 text-xs leading-relaxed text-ink-dim">
                        {m.talkingPoints.map((tp, i) => (
                          <li key={i}>{tp}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {m.codeSnippet && (
                    <div className="space-y-3 pt-1">
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-2xs font-semibold uppercase tracking-wider text-accent">
                          Source, as it is in the repository
                        </p>
                        <CopyButton text={m.codeSnippet} label="Copy" copiedLabel="Copied" />
                      </div>
                      <pre className="overflow-x-auto rounded border border-line bg-panel p-3.5 font-mono text-xs leading-relaxed text-ink-dim">
                        <code>{m.codeSnippet}</code>
                      </pre>

                      {m.codeAnnotations && m.codeAnnotations.length > 0 && (
                        <div className="space-y-2">
                          <p className="text-2xs font-semibold uppercase tracking-wider text-accent">
                            Tag sites
                          </p>
                          {m.codeAnnotations.map((anno, idx) => (
                            <div
                              key={idx}
                              className="rounded border border-line bg-panel/40 p-2.5 text-xs"
                            >
                              <div className="mb-1 flex items-center justify-between gap-2">
                                <span className="font-mono text-2xs font-semibold text-accent">
                                  {anno.lineRange}
                                </span>
                                <span className="text-xs font-semibold text-ink">{anno.title}</span>
                              </div>
                              <p className="font-mono text-2xs leading-relaxed text-ink-dim">
                                {anno.description}
                              </p>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {/* What the server actually retrieved. Retrieval you cannot
                      inspect is indistinguishable from retrieval that did not
                      happen. */}
                  {m.contextNote && (
                    <details className="rounded border border-line/60 bg-panel/60 p-2.5">
                      <summary className="cursor-pointer text-2xs font-semibold uppercase tracking-wider text-ink-faint">
                        Grounding context used
                      </summary>
                      <p className="mt-2 font-mono text-2xs leading-relaxed text-ink-dim">
                        {m.contextNote}
                      </p>
                    </details>
                  )}

                  {(m.codeRef || canNavigate(m)) && (
                    <div className="flex items-center justify-between gap-2 border-t border-line/40 pt-2 font-mono text-2xs">
                      <span className="text-ink-dim">{m.codeRef ?? ""}</span>
                      {canNavigate(m) && (
                        <button
                          type="button"
                          onClick={() => handleNavigateToCode(m)}
                          className="rounded border border-accent/40 bg-accent/10 px-2.5 py-1 font-semibold text-accent transition-colors hover:bg-accent/20"
                        >
                          Open in Defect Explorer
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>

          <form onSubmit={handleSend} className="border-t border-line bg-raised p-4">
            <div className="flex gap-2">
              <input
                type="text"
                value={inputQuery}
                onChange={(e) => setInputQuery(e.target.value)}
                maxLength={MAX_QUESTION_CHARS}
                disabled={isSending}
                placeholder={
                  mode === "live"
                    ? "Ask anything about the defects, the decisions or the metrics…"
                    : "Ask offline — answers are matched against the bundle…"
                }
                className="flex-1 rounded border border-line bg-panel px-3.5 py-2.5 text-xs text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none disabled:opacity-60"
                aria-label="Question"
              />
              <button
                type="submit"
                disabled={isSending || inputQuery.trim() === ""}
                className="rounded bg-accent px-5 py-2.5 text-xs font-semibold text-panel transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {isSending ? "Asking…" : "Send"}
              </button>
            </div>
            <p className="mt-2 text-2xs text-ink-faint">
              {mode === "live"
                ? "Live answers are grounded on retrieved bundle context and rate-limited per IP. The model is told to refuse rather than guess."
                : "Offline: answers are matched against the bundle's own catalog, audit ledger and metrics."}
            </p>
          </form>
        </div>
      )}
    </>
  );
}
