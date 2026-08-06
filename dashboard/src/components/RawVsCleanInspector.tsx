"use client";

import React from "react";
import { Badge } from "@/components/ui";
import type { Bundle } from "@/lib/types";

interface CellInfo {
  raw_value: string;
  clean_value: string;
  status: "clean" | "error" | "fixed";
  defect_code: string | null;
  explanation: string | null;
}

interface RowData {
  row_id: string;
  defects: string[];
  cells: Record<string, CellInfo>;
}

/** A `RowData` carrying the unique render key assigned at load time. */
interface KeyedRow extends RowData {
  /** Source-array position. Unique even when `row_id` is not. */
  uid: string;
}

interface DatasetDiff {
  headers: string[];
  rows: RowData[];
}

interface CsvDiffData {
  stores?: DatasetDiff;
  products?: DatasetDiff;
  transactions?: DatasetDiff;
}

interface Props {
  bundle: Bundle;
  onSelectDefect?: (code: string) => void;
}

/* ── Sorting ────────────────────────────────────────────────────────────────
 *
 * WHY ONE SORT STATE FOR TWO TABLES: the panes are side by side and their rows
 * correspond one-to-one by `row_id`. Sorting them independently would silently
 * destroy that correspondence — row 4 on the left would stop being row 4 on the
 * right — and would also break the click-to-scroll behaviour, which looks up a
 * clean-side row by id and scrolls it into view. So a click on either header
 * reorders BOTH tables identically.
 *
 * WHICH VALUE IS SORTED ON: the one in the pane you clicked. Clicking a header
 * on the raw side orders by the raw strings, which is what someone auditing the
 * source file expects; clicking on the clean side orders by the cleaned values.
 * Both produce the same row order across the two tables.
 */
type SortDirection = "asc" | "desc";
type SortSource = "raw" | "clean";

interface SortState {
  col: string;
  dir: SortDirection;
  source: SortSource;
}

/** Strip currency symbols, thousands separators and stray whitespace. */
const NUMERIC_RE = /^-?[$€£]?\s*-?[\d,]+(\.\d+)?%?$/;

/**
 * Best-effort typed value for comparison.
 *
 * WHY THIS IS NOT `String.localeCompare` ALONE: this table's whole subject is
 * messy source data. `total_amount` arrives as a mix of `142.50` and `"$142.50"`
 * (defect TX-02) and `transaction_date` in three different formats (TX-01). A
 * plain string sort puts `$99.00` after `$1,000.00` and scatters the dates,
 * which would make the sort actively misleading in the one view built to expose
 * exactly those defects.
 *
 * Returns a number for anything numeric or date-like, otherwise a lowercased
 * string. Empty values sort last in both directions — a blank is an absence,
 * not a minimum, and burying it under the data is worse than parking it at the
 * end where it can be seen.
 */
function comparableValue(raw: string): number | string | null {
  const text = (raw ?? "").trim();
  if (!text) return null;

  if (NUMERIC_RE.test(text)) {
    const numeric = Number(text.replace(/[$€£,%\s]/g, ""));
    if (Number.isFinite(numeric)) return numeric;
  }

  // Dates: ISO, US (MM/DD/YYYY) and EU (DD-MM-YYYY) all appear in this data.
  // Compared as epoch days so the three formats interleave correctly instead of
  // sorting into three separate alphabetical blocks.
  const iso = /^(\d{4})-(\d{2})-(\d{2})/.exec(text);
  if (iso) return Date.UTC(+iso[1], +iso[2] - 1, +iso[3]);
  const us = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(text);
  if (us) return Date.UTC(+us[3], +us[1] - 1, +us[2]);
  const eu = /^(\d{2})-(\d{2})-(\d{4})$/.exec(text);
  if (eu) return Date.UTC(+eu[3], +eu[2] - 1, +eu[1]);

  return text.toLowerCase();
}

function compareRows(a: RowData, b: RowData, sort: SortState): number {
  const pick = (row: RowData) => {
    const cell = row.cells[sort.col];
    if (!cell) return null;
    return comparableValue(sort.source === "raw" ? cell.raw_value : cell.clean_value);
  };

  const left = pick(a);
  const right = pick(b);

  // Empties last, regardless of direction. See comparableValue.
  if (left === null && right === null) return 0;
  if (left === null) return 1;
  if (right === null) return -1;

  let result: number;
  if (typeof left === "number" && typeof right === "number") {
    result = left - right;
  } else {
    result = String(left).localeCompare(String(right), undefined, { numeric: true });
  }
  return sort.dir === "asc" ? result : -result;
}

/**
 * A clickable column header.
 *
 * Cycles through ascending → descending → unsorted. The third state matters
 * here: the source order IS the file order, which is information — row 1 is the
 * first line of the CSV — so there has to be a way back to it without a reload.
 *
 * Rendered as a real `<button>` inside the `<th>` so it is keyboard reachable,
 * and carries `aria-sort` so the current state is announced rather than being
 * conveyed by a glyph alone.
 */
function SortableHeader({
  label,
  source,
  sort,
  onSort,
}: {
  label: string;
  source: SortSource;
  sort: SortState | null;
  onSort: (col: string, source: SortSource) => void;
}) {
  const active = sort?.col === label && sort.source === source;
  const ariaSort = active ? (sort!.dir === "asc" ? "ascending" : "descending") : "none";

  return (
    <th
      className="border-r border-line/50 p-0 font-semibold"
      aria-sort={ariaSort as React.AriaAttributes["aria-sort"]}
      scope="col"
    >
      <button
        type="button"
        onClick={() => onSort(label, source)}
        className={`flex w-full items-center gap-1 p-2 text-left transition-colors hover:bg-line/40 focus:outline-none focus-visible:ring-1 focus-visible:ring-accent ${
          active ? "text-accent" : ""
        }`}
        title={
          active
            ? `Sorted ${sort!.dir === "asc" ? "ascending" : "descending"} — click to ${
                sort!.dir === "asc" ? "reverse" : "clear"
              }`
            : `Sort by ${label}`
        }
      >
        <span className="truncate">{label}</span>
        <span aria-hidden="true" className={active ? "text-accent" : "text-ink-faint/50"}>
          {active ? (sort!.dir === "asc" ? "▲" : "▼") : "↕"}
        </span>
      </button>
    </th>
  );
}

export default function RawVsCleanInspector({ bundle, onSelectDefect }: Props) {
  const [data, setData] = React.useState<CsvDiffData | null>(null);
  const [dataset, setDataset] = React.useState<"transactions" | "products" | "stores">("transactions");
  const [viewMode, setViewMode] = React.useState<"split" | "raw" | "clean">("split");
  const [selectedCode, setSelectedCode] = React.useState<string>("all");
  const [activeCell, setActiveCell] = React.useState<{
    row_id: string;
    col: string;
    info: CellInfo;
  } | null>(null);

  const [sort, setSort] = React.useState<SortState | null>(null);
  // Keyed by `uid`, not `row_id` — see the keyedRows comment. Two duplicate
  // rows must be able to flash independently.
  const [flashingCell, setFlashingCell] = React.useState<{ uid: string; col: string } | null>(null);
  const flashTimerRef = React.useRef<NodeJS.Timeout | null>(null);
  const cleanRowRefs = React.useRef<Record<string, HTMLTableRowElement | null>>({});

  /** Cycle a column: ascending → descending → back to source (file) order. */
  const handleSort = React.useCallback((col: string, source: SortSource) => {
    setSort((current) => {
      if (!current || current.col !== col || current.source !== source) {
        return { col, source, dir: "asc" };
      }
      if (current.dir === "asc") return { col, source, dir: "desc" };
      return null;
    });
  }, []);

  const handleCellClick = (uid: string, row_id: string, col: string, info: CellInfo) => {
    setActiveCell({ row_id, col, info });

    // Set flashing cell state for 15 seconds
    setFlashingCell({ uid, col });
    if (flashTimerRef.current) clearTimeout(flashTimerRef.current);
    flashTimerRef.current = setTimeout(() => {
      setFlashingCell(null);
    }, 15000);

    // Auto-scroll the clean table to bring corresponding row into view
    const targetRowEl = cleanRowRefs.current[row_id];
    if (targetRowEl) {
      targetRowEl.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  };

  React.useEffect(() => {
    return () => {
      if (flashTimerRef.current) clearTimeout(flashTimerRef.current);
    };
  }, []);

  // Changing dataset changes the columns, so a sort keyed to a column that no
  // longer exists would silently do nothing. Reset rather than leave a stale
  // indicator pointing at a header that is gone.
  React.useEffect(() => {
    setSort(null);
  }, [dataset]);

  React.useEffect(() => {
    fetch("/data/csv_diff.json")
      .then((r) => r.json())
      .then((d) => setData(d))
      .catch(() => console.error("Failed to load csv_diff.json"));
  }, []);

  const currentDataset = data?.[dataset];

  /*
   * A STABLE, UNIQUE KEY PER ROW — and why this is not cosmetic.
   *
   * `row_id` is the transaction id, and it is NOT unique: the 15 TX-09 rows are
   * *exact duplicates*, so they carry the same id by definition. That is the
   * defect this view exists to display.
   *
   * Using it as a React `key` therefore hands React 505 keys of which 490 are
   * distinct. React then cannot tell those 15 rows apart across a re-render, so
   * on sort it reuses the wrong DOM nodes: the duplicated block refuses to
   * reorder while unique rows sort correctly, and a second click appears to do
   * nothing at all. That is exactly the "sorts a bit, but not really" behaviour
   * observed on the deployed build.
   *
   * `uid` is the row's position in the SOURCE array — unique by construction,
   * assigned once, and unchanged by sorting or filtering. It also fixes the
   * clean-row ref map, which was keyed by `row_id` and so collided for those
   * same 15 rows, sending scroll-to-row to whichever duplicate registered last.
   *
   * NOTE ON PLACEMENT: this hook sits ABOVE the loading early-return, and must
   * stay there. React requires the same hooks in the same order on every
   * render; a `useMemo` placed after a conditional `return` runs on some
   * renders and not others, which is React error #310 — a blank page with
   * "Application error: a client-side exception has occurred". The first
   * version of this fix made exactly that mistake and took the deployed site
   * down. Hence `?? []`: the hook always runs, and simply has nothing to map
   * until the fetch resolves.
   */
  const keyedRows: KeyedRow[] = React.useMemo(
    () => (currentDataset?.rows ?? []).map((r, sourceIndex) => ({ ...r, uid: `${sourceIndex}` })),
    [currentDataset],
  );

  if (!currentDataset) {
    return (
      <div className="rounded-lg border border-line bg-panel p-8 text-center text-ink-dim">
        Loading CSV visual diff inspection data...
      </div>
    );
  }

  const { headers } = currentDataset;

  const filteredRows = keyedRows.filter((r) => {
    if (selectedCode === "all") return true;
    return r.defects.includes(selectedCode);
  });

  // One ordering, applied to both panes. `slice()` first because `sort` mutates
  // in place and `filteredRows` is derived from the loaded dataset — sorting it
  // directly would permanently scramble the source order this view lets you
  // return to.
  const displayRows = sort ? filteredRows.slice().sort((a, b) => compareRows(a, b, sort)) : filteredRows;

  const allCodes = Array.from(new Set(keyedRows.flatMap((r) => r.defects))).sort();

  return (
    <div className="space-y-6">
      {/* Header Bar */}
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-line bg-panel p-4">
        <div>
          <h2 className="text-lg font-bold text-ink">Raw vs. Clean CSV Visual Inspector</h2>
          <p className="text-xs text-ink-dim">
            Inspect raw source CSVs with errors highlighted in <span className="text-red-400 font-semibold">Red</span>, side-by-side with pipeline transformations in <span className="text-green-400 font-semibold">Green</span>.
          </p>
        </div>

        {/* Controls */}
        <div className="flex flex-wrap items-center gap-3">
          {/* Dataset Switcher */}
          <div className="inline-flex rounded-lg border border-line bg-raised p-1">
            {(["transactions", "products", "stores"] as const).map((ds) => (
              <button
                key={ds}
                type="button"
                onClick={() => setDataset(ds)}
                className={`rounded px-3 py-1 text-xs font-semibold capitalize transition-colors ${
                  dataset === ds ? "bg-accent text-accent-contrast" : "text-ink-dim hover:text-ink"
                }`}
              >
                {ds}
              </button>
            ))}
          </div>

          {/* View Mode */}
          <div className="inline-flex rounded-lg border border-line bg-raised p-1">
            {(["split", "raw", "clean"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => setViewMode(mode)}
                className={`rounded px-2.5 py-1 text-xs font-semibold capitalize transition-colors ${
                  viewMode === mode ? "bg-accent text-accent-contrast" : "text-ink-dim hover:text-ink"
                }`}
              >
                {mode === "split" ? "Split View" : mode === "raw" ? "Raw Only (Red)" : "Clean Only (Green)"}
              </button>
            ))}
          </div>

          {/* Defect Code Filter */}
          <select
            value={selectedCode}
            onChange={(e) => setSelectedCode(e.target.value)}
            className="rounded-lg border border-line bg-raised px-3 py-1.5 text-xs text-ink focus:outline-none focus:ring-1 focus:ring-accent"
          >
            <option value="all">All Rows ({keyedRows.length})</option>
            {allCodes.map((code) => (
              <option key={code} value={code}>
                Filter by {code}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Popover Detail Modal / Card when cell is clicked */}
      {activeCell && (
        <div className="rounded-lg border border-accent/40 bg-raised p-4 shadow-lg transition-all space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Badge tone={activeCell.info.status === "error" ? "bad" : "accent"}>
                {activeCell.info.defect_code ?? "INFO"}
              </Badge>
              <span className="font-mono text-xs font-bold text-ink">
                Row: {activeCell.row_id} · Column: {activeCell.col}
              </span>
            </div>
            <button
              type="button"
              onClick={() => setActiveCell(null)}
              className="text-xs text-ink-dim hover:text-ink"
            >
              ✕ Close
            </button>
          </div>

          {/* Flashing Green Fix & Explanation Banner */}
          {flashingCell && (
            <div className="flex flex-wrap items-center gap-2 rounded border border-green-500/50 bg-green-500/20 px-3 py-2 text-xs text-green-300 font-mono animate-pulse">
              <span className="animate-spin">⚡</span>
              <span className="font-bold uppercase tracking-wider text-green-400">Live Fix Trace (Flashing 15s)</span>
              <span>·</span>
              <span className="font-semibold text-red-300">Raw: {activeCell.info.raw_value || "(empty)"}</span>
              <span>➔</span>
              <span className="font-semibold text-green-200">Cleaned: {activeCell.info.clean_value || "(empty)"}</span>
            </div>
          )}

          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 text-xs">
            <div className="rounded border border-red-500/30 bg-red-500/10 p-2.5">
              <span className="block font-semibold text-red-400">Raw Value:</span>
              <code className="font-mono text-ink">{activeCell.info.raw_value || "(empty)"}</code>
            </div>
            <div className="rounded border border-green-500/30 bg-green-500/10 p-2.5">
              <span className="block font-semibold text-green-400">Cleaned Value:</span>
              <code className="font-mono text-ink">{activeCell.info.clean_value || "(empty)"}</code>
            </div>
          </div>

          <p className="mt-3 text-xs text-ink-dim leading-relaxed">
            {activeCell.info.explanation || "Standardized by cleaning rules."}
          </p>

          {activeCell.info.defect_code && onSelectDefect && (
            <div className="mt-3 flex justify-end">
              <button
                type="button"
                onClick={() => onSelectDefect(activeCell.info.defect_code!)}
                className="rounded bg-accent px-3 py-1.5 text-xs font-semibold text-accent-contrast hover:bg-accent/90"
              >
                View {activeCell.info.defect_code} in Defect Explorer →
              </button>
            </div>
          )}
        </div>
      )}

      {/* Grids Container */}
      <div className={`grid gap-6 ${viewMode === "split" ? "grid-cols-1 lg:grid-cols-2" : "grid-cols-1"}`}>
        {/* Raw View Grid */}
        {(viewMode === "split" || viewMode === "raw") && (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs font-semibold text-red-400">
              <span>Original Raw CSV ({dataset}.csv)</span>
              <span>Red cells = Seeded Defects</span>
            </div>
            <div className="max-h-[600px] overflow-auto rounded-lg border border-line bg-panel">
              <table className="w-full text-left text-xs">
                <thead className="sticky top-0 bg-raised border-b border-line text-ink-dim font-mono">
                  <tr>
                    <th className="p-2 border-r border-line/50">#</th>
                    {headers.map((h) => (
                      <SortableHeader
                        key={h}
                        label={h}
                        source="raw"
                        sort={sort}
                        onSort={handleSort}
                      />
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/40 font-mono">
                  {displayRows.map((r, i) => (
                    <tr key={r.uid} className="hover:bg-raised/40">
                      <td className="p-2 border-r border-line/50 text-ink-faint text-2xs">{i + 1}</td>
                      {headers.map((h) => {
                        const cell = r.cells[h] ?? { raw_value: "", status: "clean" };
                        const isErr = cell.status === "error" || cell.status === "fixed";
                        return (
                          <td
                            key={h}
                            onClick={() => isErr && handleCellClick(r.uid, r.row_id, h, cell)}
                            className={`p-2 border-r border-line/50 transition-colors ${
                              isErr
                                ? "cursor-pointer bg-red-500/15 text-red-300 font-semibold hover:bg-red-500/25"
                                : "text-ink-dim"
                            }`}
                            title={isErr ? `Click to inspect ${cell.defect_code}` : undefined}
                          >
                            {cell.raw_value}
                            {cell.defect_code && (
                              <span className="ml-1 rounded bg-red-500/30 px-1 py-0.5 text-3xs font-bold text-red-200">
                                {cell.defect_code}
                              </span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Clean View Grid */}
        {(viewMode === "split" || viewMode === "clean") && (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs font-semibold text-green-400">
              <span>Cleaned Pipeline Output ({dataset}_clean.csv)</span>
              <span>Green cells = Transformed / Imputed</span>
            </div>
            <div className="max-h-[600px] overflow-auto rounded-lg border border-line bg-panel">
              <table className="w-full text-left text-xs">
                <thead className="sticky top-0 bg-raised border-b border-line text-ink-dim font-mono">
                  <tr>
                    <th className="p-2 border-r border-line/50">#</th>
                    {headers.map((h) => (
                      <SortableHeader
                        key={h}
                        label={h}
                        source="clean"
                        sort={sort}
                        onSort={handleSort}
                      />
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/40 font-mono">
                  {displayRows.map((r, i) => (
                    <tr
                      key={r.uid}
                      ref={(el) => {
                        cleanRowRefs.current[r.uid] = el;
                      }}
                      className="hover:bg-raised/40 transition-colors"
                    >
                      <td className="p-2 border-r border-line/50 text-ink-faint text-2xs">{i + 1}</td>
                      {headers.map((h) => {
                        const cell = r.cells[h] ?? { clean_value: "", status: "clean" };
                        const isFixed = cell.status === "fixed" || cell.status === "error";
                        const isFlashing = flashingCell?.uid === r.uid && flashingCell?.col === h;

                        return (
                          <td
                            key={h}
                            onClick={() => isFixed && handleCellClick(r.uid, r.row_id, h, cell)}
                            className={`p-2 border-r border-line/50 transition-all duration-300 ${
                              isFlashing
                                ? "bg-green-500/40 text-green-100 font-bold ring-4 ring-green-400 animate-pulse shadow-xl shadow-green-500/50 z-10"
                                : isFixed
                                  ? "cursor-pointer bg-green-500/15 text-green-300 font-semibold hover:bg-green-500/25"
                                  : "text-ink-dim"
                            }`}
                            title={isFixed ? `Click to inspect fix` : undefined}
                          >
                            {cell.clean_value}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
