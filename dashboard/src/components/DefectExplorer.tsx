"use client";

import React from "react";

import DefectDetail from "@/components/DefectDetail";
import { Badge, EmptyState, SectionHeader, SeverityBadge } from "@/components/ui";
import { ACTION_LABELS, SEVERITY_ORDER } from "@/lib/config";
import { formatInt } from "@/lib/format";
import type { Bundle, DefectView } from "@/lib/types";

/**
 * Defect Explorer — the centrepiece.
 *
 * Left: a filterable, sortable table of all defect classes.
 * Right: the detail panel for the selected one, ending in the exact source
 *        lines that handle it.
 *
 * Selection lives in the URL (`#defects/TX-03`), not in this component, so a
 * selection is always shareable and the browser back button steps through the
 * defects a reviewer looked at.
 */

type SortKey = "code" | "severity" | "dataset" | "detected" | "expected";
type SortDir = "asc" | "desc";

const SEVERITY_RANK: Record<string, number> = Object.fromEntries(
  SEVERITY_ORDER.map((s, i) => [s, i]),
);

export default function DefectExplorer({
  bundle,
  defects,
  selectedCode,
  codeFilter,
  onSelectDefect,
}: {
  bundle: Bundle;
  defects: DefectView[];
  selectedCode: string | null;
  /** Code allow-list pushed in from the Lineage view via the URL. */
  codeFilter: string[] | null;
  onSelectDefect: (code: string) => void;
}) {
  const [dataset, setDataset] = React.useState<string>("all");
  const [severity, setSeverity] = React.useState<string>("all");
  const [query, setQuery] = React.useState<string>("");
  const [sortKey, setSortKey] = React.useState<SortKey>("code");
  const [sortDir, setSortDir] = React.useState<SortDir>("asc");

  const datasets = React.useMemo(
    () => Array.from(new Set(defects.map((d) => d.dataset))).sort(),
    [defects],
  );

  const rows = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    const allowed = codeFilter ? new Set(codeFilter) : null;

    const filtered = defects.filter((d) => {
      if (allowed && !allowed.has(d.code)) return false;
      if (dataset !== "all" && d.dataset !== dataset) return false;
      if (severity !== "all" && d.severity !== severity) return false;
      if (!q) return true;
      // Free-text search spans the reasoning fields too: a reviewer looking for
      // "survivorship" or "NULLIF" should land on the right row.
      return [d.code, d.title, d.detection, d.decision, d.rationale, d.dataset]
        .join(" ")
        .toLowerCase()
        .includes(q);
    });

    const dir = sortDir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      switch (sortKey) {
        case "severity":
          return (SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]) * dir;
        case "dataset":
          return a.dataset.localeCompare(b.dataset) * dir || a.code.localeCompare(b.code);
        case "detected":
          return ((a.detected_count ?? -1) - (b.detected_count ?? -1)) * dir;
        case "expected":
          return ((a.expected_count ?? -1) - (b.expected_count ?? -1)) * dir;
        default:
          return a.code.localeCompare(b.code) * dir;
      }
    });
  }, [defects, dataset, severity, query, sortKey, sortDir, codeFilter]);

  // Resolve the selected defect. Falls back to the first visible row so the
  // detail panel is never empty when there is something to show.
  const selected =
    defects.find((d) => d.code === selectedCode) ?? rows[0] ?? null;

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  const clearFilters = () => {
    setDataset("all");
    setSeverity("all");
    setQuery("");
    if (codeFilter) window.location.hash = "#defects";
  };

  const filtersActive =
    dataset !== "all" || severity !== "all" || query.trim() !== "" || !!codeFilter;

  return (
    <div className="space-y-5">
      <SectionHeader
        title="Defect Explorer"
        subtitle="Every defect class the challenge seeds, what the pipeline detected, the decision taken, and the exact tagged source line that takes it. Select a row to drill in."
        right={
          <span className="font-mono text-xs text-ink-dim">
            {formatInt(rows.length)} of {formatInt(defects.length)}
          </span>
        }
      />

      {/* ── Filters ────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label
            htmlFor="filter-dataset"
            className="block text-2xs font-medium uppercase tracking-wider text-ink-faint"
          >
            Dataset
          </label>
          <select
            id="filter-dataset"
            value={dataset}
            onChange={(e) => setDataset(e.target.value)}
            className="mt-1 rounded border border-line bg-panel px-2 py-1.5 text-sm text-ink"
          >
            <option value="all">All datasets</option>
            {datasets.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label
            htmlFor="filter-severity"
            className="block text-2xs font-medium uppercase tracking-wider text-ink-faint"
          >
            Severity
          </label>
          <select
            id="filter-severity"
            value={severity}
            onChange={(e) => setSeverity(e.target.value)}
            className="mt-1 rounded border border-line bg-panel px-2 py-1.5 text-sm text-ink"
          >
            <option value="all">All severities</option>
            {SEVERITY_ORDER.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>

        <div className="min-w-[16rem] flex-1">
          <label
            htmlFor="filter-query"
            className="block text-2xs font-medium uppercase tracking-wider text-ink-faint"
          >
            Search titles, detection, decisions and rationale
          </label>
          <input
            id="filter-query"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g. survivorship, discount, NULLIF, orphan"
            className="mt-1 w-full rounded border border-line bg-panel px-2 py-1.5 text-sm text-ink placeholder:text-ink-faint"
          />
        </div>

        {filtersActive && (
          <button
            type="button"
            onClick={clearFilters}
            className="rounded border border-line bg-raised px-2.5 py-1.5 text-xs text-ink-dim transition-colors hover:border-line-strong hover:text-ink"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Explains where a code filter came from when the Lineage view set it. */}
      {codeFilter && (
        <p className="text-xs text-ink-dim">
          Filtered to {codeFilter.length} defect code(s) owned by the pipeline stage you came from:{" "}
          <span className="font-mono text-ink">{codeFilter.join(", ")}</span>
        </p>
      )}

      {/* ── Table + detail ─────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
        {/* Table */}
        <div className="panel overflow-hidden">
          <div
            tabIndex={0}
            role="region"
            aria-label="Defect classes"
            className="max-h-[38rem] overflow-auto"
          >
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">
                Defect classes, filterable and sortable. Select a code to open its detail panel.
              </caption>
              <thead className="sticky top-0 z-10 border-b border-line bg-panel">
                <tr>
                  <SortableTh label="Code" k="code" sortKey={sortKey} dir={sortDir} onSort={toggleSort} className="w-20" />
                  <th scope="col" className="th">Title</th>
                  <SortableTh label="Sev" k="severity" sortKey={sortKey} dir={sortDir} onSort={toggleSort} className="w-24" />
                  <SortableTh label="Det" k="detected" sortKey={sortKey} dir={sortDir} onSort={toggleSort} className="w-16 text-right" />
                  <SortableTh label="Exp" k="expected" sortKey={sortKey} dir={sortDir} onSort={toggleSort} className="w-16 text-right" />
                  <th scope="col" className="th w-28">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((d) => {
                  const active = selected?.code === d.code;
                  const ok = d.coverage === "match";
                  return (
                    <tr
                      key={d.code}
                      onClick={() => onSelectDefect(d.code)}
                      aria-selected={active}
                      className={`cursor-pointer transition-colors ${
                        active ? "bg-accent/10" : "hover:bg-raised/60"
                      }`}
                    >
                      <td className="td">
                        {/* The anchor, not the row, is the focusable control —
                            keyboard users get real link semantics, mouse users
                            get the whole row as a target. */}
                        <a
                          href={`#defects/${d.code}`}
                          onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            onSelectDefect(d.code);
                          }}
                          className={`font-mono font-medium ${active ? "text-accent" : "text-ink"} hover:underline`}
                        >
                          {d.code}
                        </a>
                      </td>
                      <td className="td">
                        <span className={active ? "text-ink" : ""}>{d.title}</span>
                        <span className="mt-0.5 block font-mono text-2xs text-ink-faint">
                          {d.dataset}
                        </span>
                      </td>
                      <td className="td"><SeverityBadge severity={d.severity} /></td>
                      <td
                        className={`td text-right font-mono tabular-nums ${ok ? "" : "text-bad"}`}
                      >
                        {d.detected_count === null ? "—" : formatInt(d.detected_count)}
                      </td>
                      <td className="td text-right font-mono tabular-nums text-ink-faint">
                        {d.expected_count === null ? "var" : formatInt(d.expected_count)}
                      </td>
                      <td className="td">
                        {d.audit ? (
                          <Badge tone={ok ? "neutral" : "bad"}>
                            {ACTION_LABELS[d.audit.action] ?? d.audit.action}
                          </Badge>
                        ) : (
                          <Badge tone="bad">not reported</Badge>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {rows.length === 0 && (
            <div className="px-4 py-8 text-center">
              <p className="text-sm text-ink-dim">No defect classes match these filters.</p>
              <button
                type="button"
                onClick={clearFilters}
                className="mt-2 text-xs text-accent hover:underline"
              >
                Clear filters
              </button>
            </div>
          )}
        </div>

        {/* Detail */}
        {selected ? (
          <DefectDetail defect={selected} sourceFiles={bundle.source_files ?? {}} />
        ) : (
          <EmptyState
            title="Nothing selected"
            detail="Select a defect class on the left to see its decision record and handling code."
          />
        )}
      </div>
    </div>
  );
}

/** Header cell that toggles sort direction and announces state to assistive tech. */
function SortableTh({
  label,
  k,
  sortKey,
  dir,
  onSort,
  className = "",
}: {
  label: string;
  k: SortKey;
  sortKey: SortKey;
  dir: SortDir;
  onSort: (k: SortKey) => void;
  className?: string;
}) {
  const active = sortKey === k;
  return (
    <th
      scope="col"
      className={`th ${className}`}
      aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}
    >
      <button
        type="button"
        onClick={() => onSort(k)}
        className="inline-flex items-center gap-1 uppercase tracking-wider hover:text-ink-dim"
      >
        {label}
        <span aria-hidden="true" className={active ? "text-accent" : "text-ink-faint/40"}>
          {active ? (dir === "asc" ? "▲" : "▼") : "▲"}
        </span>
      </button>
    </th>
  );
}
