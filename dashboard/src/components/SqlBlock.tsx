"use client";

import { Highlight, type PrismTheme } from "prism-react-renderer";
import React from "react";

import { CopyButton } from "@/components/ui";

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

function parseSqlClauses(sql: string) {
  const lines = sql.split("\n");
  let currentClause = "SELECT";
  const clauses: Record<string, string[]> = {
    SELECT: [],
    FROM: [],
    JOIN: [],
    WHERE: [],
    GROUP_BY: [],
    ORDER_BY: [],
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("--")) continue;

    const upper = trimmed.toUpperCase();
    if (upper.startsWith("SELECT")) {
      currentClause = "SELECT";
      clauses.SELECT.push(trimmed.replace(/^SELECT\s+/i, ""));
    } else if (upper.startsWith("FROM")) {
      currentClause = "FROM";
      clauses.FROM.push(trimmed.replace(/^FROM\s+/i, ""));
    } else if (upper.includes("JOIN")) {
      currentClause = "JOIN";
      clauses.JOIN.push(trimmed);
    } else if (upper.startsWith("WHERE") || upper.startsWith("AND ") || upper.startsWith("OR ")) {
      currentClause = "WHERE";
      clauses.WHERE.push(trimmed);
    } else if (upper.startsWith("GROUP BY")) {
      currentClause = "GROUP_BY";
      clauses.GROUP_BY.push(trimmed.replace(/^GROUP BY\s+/i, ""));
    } else if (upper.startsWith("ORDER BY")) {
      currentClause = "ORDER_BY";
      clauses.ORDER_BY.push(trimmed.replace(/^ORDER BY\s+/i, ""));
    } else {
      if (clauses[currentClause]) {
        clauses[currentClause].push(trimmed);
      }
    }
  }

  return clauses;
}

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
  const [showDeconstructed, setShowDeconstructed] = React.useState(false);

  if (!sql?.trim()) {
    return (
      <p className="rounded border border-dashed border-line px-3 py-2 text-xs text-ink-faint">
        No SQL carried in the bundle for this metric.
      </p>
    );
  }

  const clauses = parseSqlClauses(sql.trim());

  return (
    <div className="space-y-2">
      <details open={defaultOpen} className="group rounded-md border border-line bg-[#0b0d11]">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-md px-3 py-2 text-xs text-ink-dim transition-colors hover:text-ink">
          <span className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="inline-block transition-transform group-open:rotate-90"
            >
              ▸
            </span>
            <span className="font-semibold text-accent">SQL Query Engine</span>
            {sqlRef && <code className="font-mono text-2xs text-ink-faint">{sqlRef}</code>}
          </span>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={(e) => {
                e.preventDefault();
                setShowDeconstructed((prev) => !prev);
              }}
              className="rounded bg-accent/20 border border-accent/40 px-2 py-0.5 font-mono text-3xs font-semibold text-accent hover:bg-accent/30 transition-colors"
            >
              {showDeconstructed ? "Hide clause breakdown" : "Break this query down clause by clause"}
            </button>
            <span className="font-mono text-2xs text-ink-faint">
              {sql.split("\n").length} lines
            </span>
          </div>
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

      {/* Interactive SQL Deconstructor Card */}
      {showDeconstructed && (
        <div className="rounded-md border border-accent/40 bg-raised p-4 space-y-3 font-mono text-xs">
          <div className="flex items-center justify-between border-b border-line pb-2">
            <span className="font-semibold text-accent">SQL clause breakdown</span>
            <span className="text-2xs text-ink-faint">Star Schema SQLite Engine</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-2xs">
            {/* SELECT Clause */}
            <div className="rounded border border-blue-500/30 bg-blue-500/10 p-2.5 space-y-1">
              <span className="block font-semibold text-blue-400">SELECT — calculated business metrics</span>
              <ul className="list-disc list-inside text-ink space-y-0.5">
                {clauses.SELECT.map((c, i) => (
                  <li key={i} className="truncate">{c}</li>
                ))}
              </ul>
            </div>

            {/* JOIN Clause */}
            <div className="rounded border border-purple-500/30 bg-purple-500/10 p-2.5 space-y-1">
              <span className="block font-semibold text-purple-400">JOIN — star schema links</span>
              {clauses.JOIN.length > 0 ? (
                <ul className="list-disc list-inside text-ink space-y-0.5">
                  {clauses.JOIN.map((c, i) => (
                    <li key={i} className="truncate">{c}</li>
                  ))}
                </ul>
              ) : (
                <span className="text-ink-faint font-sans italic">Direct table query (fact_sales)</span>
              )}
            </div>

            {/* WHERE Clause */}
            <div className="rounded border border-green-500/30 bg-green-500/10 p-2.5 space-y-1">
              <span className="block font-semibold text-green-400">WHERE — data quality guards</span>
              {clauses.WHERE.length > 0 ? (
                <ul className="list-disc list-inside text-ink space-y-0.5">
                  {clauses.WHERE.map((c, i) => (
                    <li key={i} className="truncate">{c}</li>
                  ))}
                </ul>
              ) : (
                <span className="text-ink-faint font-sans italic">All valid fact rows included</span>
              )}
            </div>

            {/* GROUP BY Clause */}
            <div className="rounded border border-amber-500/30 bg-amber-500/10 p-2.5 space-y-1">
              <span className="block font-semibold text-amber-400">GROUP BY — business grain</span>
              {clauses.GROUP_BY.length > 0 ? (
                <ul className="list-disc list-inside text-ink space-y-0.5">
                  {clauses.GROUP_BY.map((c, i) => (
                    <li key={i} className="truncate">{c}</li>
                  ))}
                </ul>
              ) : (
                <span className="text-ink-faint font-sans italic">Overall aggregation level</span>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
