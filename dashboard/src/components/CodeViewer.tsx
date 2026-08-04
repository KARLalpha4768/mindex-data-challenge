"use client";

import { Highlight, type PrismTheme } from "prism-react-renderer";
import React from "react";

import { CopyButton } from "@/components/ui";
import { githubBlobUrl } from "@/lib/config";
import type { CodeRef, SourceFile } from "@/lib/types";

/**
 * Syntax-highlighted source viewer, scrolled to and highlighting the exact
 * lines that carry a `# DEFECT: <CODE>` tag.
 *
 * WHY prism-react-renderer AND NOT shiki
 * --------------------------------------
 * Both were on the table. shiki produces better colour (real TextMate grammars)
 * but is a build-time tool: to use it here we would have to tokenise every
 * source file during `next build` and serialise the resulting token trees into
 * the static HTML — for the ~600 lines of pipeline source in the bundle that is
 * several hundred KB of themed tokens shipped to a reader who will open two or
 * three files. Running shiki in the browser instead means pulling its WASM
 * regex engine plus grammars, which is worse.
 *
 * prism-react-renderer is ~10KB, bundles the Python and SQL grammars we need,
 * and — the deciding factor — hands us tokens PER LINE. This component needs
 * per-line control anyway, for line numbers, the highlight band, and the scroll
 * target. With shiki we would be parsing its HTML output back apart to get the
 * same thing.
 *
 * Trade-off accepted: slightly coarser tokenisation than shiki. Irrelevant at
 * this file size, and the reader is here for the comments, not the colouring.
 */

/**
 * Theme, written to match the app palette rather than imported from
 * prism-react-renderer's presets — every preset ships its own background and
 * hues that would fight the surrounding UI.
 *
 * The important choice: comments are NOT dimmed. In this codebase the comments
 * carry the reasoning a reviewer came to read, so they get near-body contrast
 * and the keywords stay subordinate. That is the opposite of a normal editor
 * theme, and it is deliberate.
 */
const theme: PrismTheme = {
  plain: { color: "#c9d1d9", backgroundColor: "transparent" },
  styles: [
    { types: ["comment", "prolog", "doctype", "cdata"], style: { color: "#8b9bb4", fontStyle: "italic" } },
    { types: ["punctuation"], style: { color: "#6c7480" } },
    { types: ["property", "tag", "boolean", "number", "constant", "symbol", "deleted"], style: { color: "#d9b23c" } },
    { types: ["selector", "attr-name", "string", "char", "builtin", "inserted"], style: { color: "#7fd1a0" } },
    { types: ["operator", "entity", "url", "variable"], style: { color: "#c9d1d9" } },
    { types: ["atrule", "attr-value", "keyword"], style: { color: "#7fa8ff" } },
    { types: ["function", "class-name"], style: { color: "#c99bff" } },
    { types: ["regex", "important"], style: { color: "#f0883e" } },
    { types: ["decorator"], style: { color: "#f0883e" } },
  ],
};

/** Map bundle language names onto Prism grammar names. */
function prismLanguage(language: string): string {
  const map: Record<string, string> = {
    python: "python",
    py: "python",
    sql: "sql",
    javascript: "javascript",
    typescript: "typescript",
  };
  return map[language?.toLowerCase()] ?? "python";
}

interface Props {
  /** Defect code being viewed — used only for labelling. */
  code: string;
  /** Every tag site for this defect, possibly across several files. */
  refs: CodeRef[];
  /** The full source of every file the bundle carries. */
  sourceFiles: Record<string, SourceFile>;
}

export default function CodeViewer({ code, refs, sourceFiles }: Props) {
  // Distinct file paths this defect is tagged in, in first-appearance order.
  const paths = React.useMemo(() => {
    const seen: string[] = [];
    for (const r of refs) if (!seen.includes(r.path)) seen.push(r.path);
    return seen;
  }, [refs]);

  const [activePath, setActivePath] = React.useState<string | null>(paths[0] ?? null);

  // Re-anchor when the defect changes: the previously active file may not even
  // contain the new code.
  React.useEffect(() => {
    setActivePath(paths[0] ?? null);
  }, [paths]);

  const scrollRef = React.useRef<HTMLDivElement | null>(null);
  const targetRef = React.useRef<HTMLDivElement | null>(null);

  const file = activePath ? sourceFiles[activePath] : undefined;

  /** 1-based line numbers to highlight in the active file. */
  const highlighted = React.useMemo(() => {
    const set = new Set<number>();
    for (const r of refs) if (r.path === activePath) set.add(r.line);
    return set;
  }, [refs, activePath]);

  const firstLine = React.useMemo(
    () => (highlighted.size ? Math.min(...highlighted) : null),
    [highlighted],
  );

  /**
   * Scroll the tagged line into view within the viewer's own scroll container.
   *
   * `scrollIntoView` is avoided on purpose: it would scroll the PAGE as well,
   * yanking the reader away from the detail panel. Setting `scrollTop` directly
   * moves only this box. The tagged line is placed roughly a third down so the
   * surrounding `# WHY:` / `# ALTERNATIVE REJECTED:` comment block — which is
   * the actual payload — is visible above it.
   */
  React.useEffect(() => {
    const container = scrollRef.current;
    const target = targetRef.current;
    if (!container || !target) return;
    const offset = target.offsetTop - container.clientHeight / 3;
    container.scrollTop = Math.max(offset, 0);
  }, [activePath, code, firstLine]);

  if (!refs.length) {
    return (
      <div className="rounded-md border border-dashed border-line px-4 py-6 text-center">
        <p className="text-sm text-ink-dim">No tagged source lines for {code}.</p>
        <p className="mt-1 text-xs text-ink-faint">
          The bundle&apos;s <code className="font-mono">code_index</code> has no entry for this
          defect. That means the pipeline source carries no{" "}
          <code className="font-mono">{`# DEFECT: ${code}`}</code> tag — either the handling is
          untagged, or it is missing.
        </p>
      </div>
    );
  }

  if (!file) {
    return (
      <div className="rounded-md border border-dashed border-line px-4 py-6 text-center">
        <p className="text-sm text-ink-dim">Source for {activePath} is not in the bundle.</p>
        <p className="mt-1 text-xs text-ink-faint">
          <code className="font-mono">code_index</code> references it but{" "}
          <code className="font-mono">source_files</code> does not carry it.
        </p>
      </div>
    );
  }

  const source = file.lines.join("\n");
  const language = prismLanguage(file.language);

  return (
    <div className="overflow-hidden rounded-md border border-line bg-[#0b0d11]">
      {/* ── Toolbar: file tabs, line reference, GitHub link, copy ──────── */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-panel px-3 py-2">
        <div className="flex flex-wrap items-center gap-1">
          {paths.map((p) => {
            const active = p === activePath;
            const count = refs.filter((r) => r.path === p).length;
            return (
              <button
                key={p}
                type="button"
                onClick={() => setActivePath(p)}
                aria-pressed={active}
                className={`rounded px-2 py-1 font-mono text-xs transition-colors ${
                  active
                    ? "bg-raised text-ink"
                    : "text-ink-faint hover:bg-raised/60 hover:text-ink-dim"
                }`}
              >
                {p}
                <span className="ml-1.5 text-ink-faint">×{count}</span>
              </button>
            );
          })}
        </div>

        <div className="flex items-center gap-2">
          <span className="font-mono text-2xs text-ink-faint">
            {highlighted.size} tagged line{highlighted.size === 1 ? "" : "s"}
            {firstLine !== null && ` · L${[...highlighted].sort((a, b) => a - b).join(", L")}`}
          </span>
          <CopyButton text={source} label="Copy file" copiedLabel="Copied" />
          {activePath && (
            <a
              href={githubBlobUrl(activePath, firstLine ?? undefined)}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex items-center rounded border border-line bg-raised px-2 py-1 text-xs text-ink-dim transition-colors hover:border-line-strong hover:text-ink"
            >
              Open on GitHub
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          )}
        </div>
      </div>

      {/* ── Source ─────────────────────────────────────────────────────── */}
      <div
        ref={scrollRef}
        tabIndex={0}
        role="region"
        aria-label={`Source of ${activePath}, highlighting lines tagged ${code}`}
        className="max-h-[28rem] overflow-auto"
      >
        <Highlight theme={theme} code={source} language={language}>
          {({ className, style, tokens, getLineProps, getTokenProps }) => (
            <pre
              className={`${className} px-0 py-2 text-[0.78rem] leading-[1.55]`}
              style={{ ...style, background: "transparent" }}
            >
              <code>
                {tokens.map((line, i) => {
                  const lineNumber = i + 1;
                  const isTagged = highlighted.has(lineNumber);
                  const lineProps = getLineProps({ line });
                  return (
                    <div
                      key={lineNumber}
                      {...lineProps}
                      ref={lineNumber === firstLine ? targetRef : undefined}
                      className={`flex ${lineProps.className ?? ""} ${
                        isTagged
                          ? "bg-accent/[0.13] shadow-[inset_2px_0_0_0_#5b9dff]"
                          : ""
                      }`}
                    >
                      {/* Line gutter. Sticky so the numbers survive horizontal
                          scrolling, which means it needs an OPAQUE background —
                          the tagged-line tint is pre-composited over the editor
                          background (#0b0d11 + 13% accent) rather than layered,
                          or code would show through it when scrolled.
                          aria-hidden: a screen reader announcing every line
                          number would drown out the code itself. */}
                      <span
                        aria-hidden="true"
                        className={`sticky left-0 w-12 shrink-0 select-none pr-3 text-right font-mono text-[0.7rem] ${
                          isTagged
                            ? "bg-[#152030] text-accent"
                            : "bg-[#0b0d11] text-ink-faint/60"
                        }`}
                      >
                        {lineNumber}
                      </span>
                      <span className="min-w-0 flex-1 whitespace-pre pr-4">
                        {line.map((token, key) => (
                          <span key={key} {...getTokenProps({ token })} />
                        ))}
                      </span>
                    </div>
                  );
                })}
              </code>
            </pre>
          )}
        </Highlight>
      </div>
    </div>
  );
}
