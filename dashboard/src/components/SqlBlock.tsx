"use client";

import { Highlight, type PrismTheme } from "prism-react-renderer";
import React from "react";

import { CopyButton } from "@/components/ui";

/**
 * Collapsible, syntax-highlighted SQL block.
 *
 * Built on `<details>/<summary>` rather than a state-driven div: the disclosure
 * is keyboard-operable, screen-reader-announced and Ctrl-F searchable for free,
 * and it degrades correctly if JavaScript never runs. Same highlighter as the
 * Python code viewer — see the justification comment in CodeViewer.tsx.
 *
 * Comments are again given near-body contrast: each query carries its
 * numerator/denominator reasoning in `--` comments, and that is the part worth
 * reading.
 */

const sqlTheme: PrismTheme = {
  plain: { color: "#c9d1d9", backgroundColor: "transparent" },
  styles: [
    { types: ["comment"], style: { color: "#8b9bb4", fontStyle: "italic" } },
    { types: ["keyword"], style: { color: "#7fa8ff" } },
    { types: ["function"], style: { color: "#c99bff" } },
    { types: ["string"], style: { color: "#7fd1a0" } },
    { types: ["number", "boolean"], style: { color: "#d9b23c" } },
    { types: ["operator", "punctuation"], style: { color: "#6c7480" } },
    { types: ["variable"], style: { color: "#f0883e" } },
  ],
};

export default function SqlBlock({
  sql,
  sqlRef,
  defaultOpen = false,
}: {
  sql?: string;
  /** e.g. "src/analytics/queries.py:RETURN_RATE_BY_STORE". */
  sqlRef?: string;
  defaultOpen?: boolean;
}) {
  if (!sql?.trim()) {
    return (
      <p className="rounded border border-dashed border-line px-3 py-2 text-xs text-ink-faint">
        No SQL carried in the bundle for this metric.
      </p>
    );
  }

  return (
    <details open={defaultOpen} className="group rounded-md border border-line bg-[#0b0d11]">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-md px-3 py-2 text-xs text-ink-dim transition-colors hover:text-ink">
        <span className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className="inline-block transition-transform group-open:rotate-90"
          >
            ▸
          </span>
          <span>SQL</span>
          {sqlRef && <code className="font-mono text-2xs text-ink-faint">{sqlRef}</code>}
        </span>
        <span className="font-mono text-2xs text-ink-faint">
          {sql.split("\n").length} lines
        </span>
      </summary>

      <div className="border-t border-line">
        <div className="flex justify-end px-3 py-2">
          <CopyButton text={sql} label="Copy SQL" copiedLabel="Copied" />
        </div>
        <div
          tabIndex={0}
          role="region"
          aria-label={sqlRef ? `SQL for ${sqlRef}` : "SQL"}
          className="max-h-96 overflow-auto px-3 pb-3"
        >
          <Highlight theme={sqlTheme} code={sql.trim()} language="sql">
            {({ className, style, tokens, getLineProps, getTokenProps }) => (
              <pre
                className={`${className} text-[0.78rem] leading-[1.55]`}
                style={{ ...style, background: "transparent" }}
              >
                <code>
                  {tokens.map((line, i) => (
                    <div key={i} {...getLineProps({ line })}>
                      {line.map((token, key) => (
                        <span key={key} {...getTokenProps({ token })} />
                      ))}
                    </div>
                  ))}
                </code>
              </pre>
            )}
          </Highlight>
        </div>
      </div>
    </details>
  );
}
