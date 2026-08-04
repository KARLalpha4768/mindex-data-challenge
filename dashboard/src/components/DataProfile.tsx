"use client";

import React from "react";

import { Badge, EmptyState, NullBar, SectionHeader, TableWrap } from "@/components/ui";
import { formatInt } from "@/lib/format";
import { type Bundle, type ColumnProfile, resolveProfilingDatasets } from "@/lib/types";

/**
 * Data Profile — the census taken BEFORE any cleaning.
 */
function dtypeTone(dtype: string, columnName: string): "neutral" | "warn" {
  const susp = String(dtype || "").toLowerCase();
  const col = String(columnName || "").toLowerCase();
  const suspicious =
    susp.includes("mixed") ||
    (susp.startsWith("object") && /(date|amount|price|qty|quantity)/i.test(col));
  return suspicious ? "warn" : "neutral";
}

export default function DataProfile({ bundle }: { bundle: Bundle }) {
  const profilingMap = resolveProfilingDatasets(bundle?.profiling);
  const datasets = Object.keys(profilingMap);

  if (datasets.length === 0) {
    return (
      <>
        <SectionHeader title="Data Profile" />
        <EmptyState
          title="No profiling data in the bundle"
          detail="Expected a `profiling` object keyed by dataset name."
        />
      </>
    );
  }

  return (
    <div className="space-y-10">
      <SectionHeader
        title="Data Profile"
        subtitle="Column-level census of the RAW files, taken before any cleaning runs. Null percentage, distinct count, range and sample values per column, plus the full-row duplicate count per dataset."
      />

      {datasets.map((name) => {
        const profile = profilingMap[name];
        if (!profile) return null;
        const columns = profile.columns ?? [];
        const nullyColumns = columns.filter((c) => (c?.null_count ?? 0) > 0).length;

        return (
          <section key={name} aria-labelledby={`profile-${name}`}>
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <h3 id={`profile-${name}`} className="font-mono text-sm font-semibold text-ink">
                {name}
              </h3>
              <Badge tone="neutral">{formatInt(profile.row_count ?? 0)} rows</Badge>
              <Badge tone="neutral">{columns.length} columns</Badge>
              <Badge tone={(profile.duplicate_row_count ?? 0) > 0 ? "warn" : "neutral"}>
                {formatInt(profile.duplicate_row_count ?? 0)} full-row duplicates
              </Badge>
              <Badge tone={nullyColumns > 0 ? "warn" : "neutral"}>
                {nullyColumns} column(s) with nulls
              </Badge>
            </div>

            <TableWrap label={`${name} column profile`}>
              <table className="w-full border-collapse text-sm">
                <thead className="border-b border-line bg-panel">
                  <tr>
                    <th scope="col" className="th">Column</th>
                    <th scope="col" className="th">Inferred dtype</th>
                    <th scope="col" className="th w-48">Null</th>
                    <th scope="col" className="th text-right">Distinct</th>
                    <th scope="col" className="th">Min</th>
                    <th scope="col" className="th">Max</th>
                    <th scope="col" className="th">Sample values</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {columns.map((col: ColumnProfile) => {
                    if (!col) return null;
                    const sampleVals = col.sample_values ?? [];
                    return (
                      <tr key={col.name ?? Math.random()} className="hover:bg-raised/40">
                        <th scope="row" className="td whitespace-nowrap font-mono font-normal text-ink">
                          {col.name ?? "—"}
                        </th>
                        <td className="td">
                          <Badge tone={dtypeTone(col.dtype ?? "", col.name ?? "")}>{col.dtype ?? "unknown"}</Badge>
                        </td>
                        <td className="td">
                          <NullBar pct={col.null_pct ?? 0} />
                          <span className="mt-0.5 block font-mono text-2xs text-ink-faint">
                            {formatInt(col.null_count ?? 0)} of {formatInt(profile.row_count ?? 0)}
                          </span>
                        </td>
                        <td className="td text-right font-mono tabular-nums">
                          {formatInt(col.distinct_count ?? 0)}
                        </td>
                        <td className="td whitespace-nowrap font-mono text-xs">
                          {col.min ?? "—"}
                        </td>
                        <td className="td whitespace-nowrap font-mono text-xs">
                          {col.max ?? "—"}
                        </td>
                        <td className="td">
                          <span className="flex flex-wrap gap-1">
                            {sampleVals.length === 0 ? (
                              <span className="text-ink-faint">—</span>
                            ) : (
                              sampleVals.slice(0, 4).map((v, i) => (
                                <code
                                  key={`${v}-${i}`}
                                  className="max-w-[12rem] truncate rounded bg-raised px-1.5 py-0.5 font-mono text-[0.7rem] text-ink-dim"
                                  title={String(v ?? "")}
                                >
                                  {v === "" || v === null || v === undefined ? "(empty)" : String(v)}
                                </code>
                              ))
                            )}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableWrap>
          </section>
        );
      })}
    </div>
  );
}
