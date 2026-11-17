"use client";

import React, { useMemo, useState } from "react";

import { Badge, CopyButton, EmptyState, SectionHeader, TableWrap } from "@/components/ui";
import { formatInt, formatMetricCell, humaniseColumn, isNumericColumn } from "@/lib/format";
import {
  listPublishedQueries,
  resolveRecordedSql,
  rowsToCsv,
  type PublishedQuery,
} from "@/lib/publishedMetrics";
import type { Bundle } from "@/lib/types";

/**
 * Recorded SQL.
 *
 * Each preset is a metric the pipeline already executed. The table under the
 * editor is that metric's recorded rows, and only while the editor still
 * holds that metric's SQL. Editing the text does not re-run anything: the
 * browser has no warehouse, and a keyword match against a handwritten table
 * is how this view previously reported S008 as a 0% return rate.
 */
export default function SqlSandbox({ bundle }: { bundle: Bundle }) {
  const metrics = bundle.analytics?.metrics ?? {};
  const queries = useMemo(() => listPublishedQueries(metrics), [metrics]);
  const [sql, setSql] = useState<string>(queries[0]?.sql ?? "");
  const [activeId, setActiveId] = useState<string>(queries[0]?.id ?? "");

  const resolution = useMemo(() => resolveRecordedSql(sql, metrics), [sql, metrics]);
  const recorded = resolution.status === "recorded" ? resolution.query : null;

  const selectQuery = (query: PublishedQuery) => {
    setActiveId(query.id);
    setSql(query.sql);
  };

  if (queries.length === 0) {
    return (
      <>
        <SectionHeader title="Recorded SQL" />
        <EmptyState
          title="No published SQL in this bundle"
          detail="The analytics block has no metric that carries both a SQL string and its rows."
        />
      </>
    );
  }

  return (
    <div className="space-y-8">
      <section aria-labelledby="sql-heading">
        <SectionHeader
          title="Recorded SQL"
          subtitle="The SQL each metric ran, and the rows that run wrote. This page does not execute SQL."
        />
        <p className="text-sm text-ink-dim">
          Pick a published metric to load its statement. The result table stays
          in step with that statement. Change the text and the table is
          withdrawn — there is no engine here to answer a different question,
          and a guessed table would be a second set of books.
        </p>
      </section>

      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
        {queries.map((query) => {
          const selected = query.id === activeId;
          return (
            <button
              key={query.id}
              type="button"
              onClick={() => selectQuery(query)}
              aria-pressed={selected}
              className={`rounded-md border p-3 text-left transition-colors ${
                selected
                  ? "border-accent/60 bg-accent/10 ring-1 ring-accent/30"
                  : "border-line bg-panel hover:border-line-strong"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-xs font-semibold text-ink">{query.title}</span>
                <Badge tone="mono">{formatInt(query.rows.length)}</Badge>
              </div>
              <p className="mt-1 line-clamp-2 text-2xs text-ink-dim">{query.description}</p>
            </button>
          );
        })}
      </div>

      <div className="panel overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2">
          <div className="flex items-center gap-2">
            <span className="font-mono text-2xs uppercase tracking-wider text-ink-faint">
              Published statement
            </span>
            {recorded ? (
              <Badge tone="ok">matches {recorded.id}</Badge>
            ) : (
              <Badge tone="warn">not a published statement</Badge>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                const current = queries.find((query) => query.id === activeId) ?? queries[0];
                setSql(current.sql);
              }}
              className="text-2xs text-ink-faint hover:text-ink"
            >
              Restore published SQL
            </button>
            <CopyButton text={sql} label="Copy SQL" copiedLabel="Copied" />
          </div>
        </div>
        <label className="block p-3">
          <span className="sr-only">Published SQL</span>
          <textarea
            value={sql}
            onChange={(event) => setSql(event.target.value)}
            rows={12}
            spellCheck={false}
            aria-label="Published SQL"
            className="w-full resize-y bg-transparent font-mono text-xs text-ink focus:outline-none"
          />
        </label>
      </div>

      {recorded ? (
        <RecordedTable query={recorded} />
      ) : (
        <EmptyState
          title="This statement was not executed"
          detail={
            resolution.status === "not_executed" && resolution.reason === "empty"
              ? "Paste or restore a published metric to see the rows the pipeline recorded for it."
              : "The editor no longer matches a metric in this bundle. Restore the published SQL to see that metric's rows. A different statement is not run in the browser."
          }
        />
      )}
    </div>
  );
}

function RecordedTable({ query }: { query: PublishedQuery }) {
  const csv = rowsToCsv(query.columns, query.rows);
  const json = JSON.stringify(query.rows, null, 2);

  return (
    <section aria-labelledby={`recorded-${query.id}`} className="space-y-3">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id={`recorded-${query.id}`} className="text-sm font-semibold text-ink">
            <a href={`#analytics/metric:${query.id}`} className="hover:text-accent hover:underline">
              {query.title}
            </a>
          </h3>
          <p className="mt-1 text-xs text-ink-dim">
            {formatInt(query.rows.length)} recorded {query.rows.length === 1 ? "row" : "rows"}
            {query.sqlRef ? (
              <>
                {" "}
                · <span className="font-mono">{query.sqlRef}</span>
              </>
            ) : null}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <CopyButton text={csv} label="Copy CSV" copiedLabel="Copied" />
          <CopyButton text={json} label="Copy JSON" copiedLabel="Copied" />
        </div>
      </header>

      {query.definitionNote && (
        <p className="rounded-md border border-accent-dim/50 bg-accent/[0.06] px-3 py-2 text-xs leading-relaxed text-ink-dim">
          {query.definitionNote}
        </p>
      )}

      <TableWrap label={`Recorded rows for ${query.id}`} maxHeight="24rem">
        <table className="w-full border-collapse text-sm">
          <thead className="sticky top-0 border-b border-line bg-panel">
            <tr>
              {query.columns.map((column) => (
                <th
                  key={column}
                  scope="col"
                  className={`th whitespace-nowrap ${
                    isNumericColumn(query.rows, column) ? "text-right" : "text-left"
                  }`}
                >
                  {humaniseColumn(column)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {query.rows.map((row, index) => (
              <tr key={index} className="hover:bg-raised/40">
                {query.columns.map((column) => {
                  const unit = query.columnUnits?.[column];
                  const rendered = formatMetricCell(column, row[column], unit);
                  const breach = column === "exceeds_threshold" && (row[column] === 1 || row[column] === true);
                  return (
                    <td
                      key={column}
                      className={`td whitespace-nowrap font-mono text-xs ${
                        isNumericColumn(query.rows, column) ? "text-right tabular-nums" : ""
                      }`}
                    >
                      {breach ? <Badge tone="bad">yes</Badge> : rendered}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
    </section>
  );
}
