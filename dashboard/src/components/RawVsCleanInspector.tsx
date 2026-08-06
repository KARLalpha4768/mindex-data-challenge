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

  const [flashingCell, setFlashingCell] = React.useState<{ row_id: string; col: string } | null>(null);
  const flashTimerRef = React.useRef<NodeJS.Timeout | null>(null);
  
  const [sortCol, setSortCol] = React.useState<string | null>(null);
  const [sortDir, setSortDir] = React.useState<"asc" | "desc">("asc");

  const rawContainerRef = React.useRef<HTMLDivElement | null>(null);
  const cleanContainerRef = React.useRef<HTMLDivElement | null>(null);
  const rawRowRefs = React.useRef<Record<string, HTMLTableRowElement | null>>({});
  const cleanRowRefs = React.useRef<Record<string, HTMLTableRowElement | null>>({});
  const isSyncingScroll = React.useRef<boolean>(false);

  const handleHeaderClick = (colName: string) => {
    if (sortCol === colName) {
      if (sortDir === "asc") {
        setSortDir("desc");
      } else {
        setSortCol(null);
        setSortDir("asc");
      }
    } else {
      setSortCol(colName);
      setSortDir("asc");
    }
  };

  React.useEffect(() => {
    setSortCol(null);
  }, [dataset]);

  const syncScroll = (source: "raw" | "clean") => {
    if (isSyncingScroll.current) return;
    isSyncingScroll.current = true;
    
    const srcEl = source === "raw" ? rawContainerRef.current : cleanContainerRef.current;
    const targetEl = source === "raw" ? cleanContainerRef.current : rawContainerRef.current;
    
    if (srcEl && targetEl) {
      targetEl.scrollTop = srcEl.scrollTop;
    }
    
    requestAnimationFrame(() => {
      isSyncingScroll.current = false;
    });
  };

  const handleCellClick = (row_id: string, col: string, info: CellInfo) => {
    setActiveCell({ row_id, col, info });

    // Set flashing cell state for 15 seconds
    setFlashingCell({ row_id, col });
    if (flashTimerRef.current) clearTimeout(flashTimerRef.current);
    flashTimerRef.current = setTimeout(() => {
      setFlashingCell(null);
    }, 15000);

    // Auto-scroll both raw and clean rows into view center
    const rawRowEl = rawRowRefs.current[row_id];
    const cleanRowEl = cleanRowRefs.current[row_id];

    if (rawRowEl) {
      rawRowEl.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    if (cleanRowEl) {
      cleanRowEl.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  };

  React.useEffect(() => {
    return () => {
      if (flashTimerRef.current) clearTimeout(flashTimerRef.current);
    };
  }, []);

  React.useEffect(() => {
    fetch("/data/csv_diff.json")
      .then((r) => r.json())
      .then((d) => setData(d))
      .catch(() => console.error("Failed to load csv_diff.json"));
  }, []);

  const currentDataset = data?.[dataset];
  if (!currentDataset) {
    return (
      <div className="rounded-lg border border-line bg-panel p-8 text-center text-ink-dim">
        Loading CSV visual diff inspection data...
      </div>
    );
  }

  const { headers = [], rows = [] } = currentDataset;

  const filteredRows = React.useMemo(() => {
    return rows.filter((r) => {
      if (!r) return false;
      if (selectedCode === "all") return true;
      return Array.isArray(r.defects) && r.defects.includes(selectedCode);
    });
  }, [rows, selectedCode]);

  const allCodes = React.useMemo(() => {
    return Array.from(new Set(rows.flatMap((r) => r?.defects || []))).sort();
  }, [rows]);

  const sortedRows = React.useMemo(() => {
    if (!sortCol) return filteredRows;
    return [...filteredRows].sort((a, b) => {
      if (!a || !b) return 0;
      const cellA = a.cells?.[sortCol];
      const cellB = b.cells?.[sortCol];
      const valA = cellA?.clean_value ?? cellA?.raw_value ?? "";
      const valB = cellB?.clean_value ?? cellB?.raw_value ?? "";

      const numA = parseFloat(String(valA).replace(/[^0-9.-]+/g, ""));
      const numB = parseFloat(String(valB).replace(/[^0-9.-]+/g, ""));
      const isNumA = !isNaN(numA) && String(valA).trim() !== "";
      const isNumB = !isNaN(numB) && String(valB).trim() !== "";

      let cmp = 0;
      if (isNumA && isNumB) {
        cmp = numA - numB;
      } else {
        cmp = String(valA).localeCompare(String(valB), undefined, { numeric: true, sensitivity: "base" });
      }

      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [filteredRows, sortCol, sortDir]);

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
            <option value="all">All Rows ({rows.length})</option>
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
              <span>Red cells = Seeded Defects {sortCol && <span className="ml-2 text-accent">(Sorted by {sortCol} {sortDir === "asc" ? "▲" : "▼"})</span>}</span>
            </div>
            <div
              ref={rawContainerRef}
              onScroll={() => syncScroll("raw")}
              className="max-h-[600px] overflow-auto rounded-lg border border-line bg-panel scroll-smooth"
            >
              <table className="w-full text-left text-xs">
                <thead className="sticky top-0 bg-raised border-b border-line text-ink-dim font-mono z-20">
                  <tr>
                    <th
                      onClick={() => setSortCol(null)}
                      className="p-2 border-r border-line/50 whitespace-nowrap cursor-pointer hover:bg-raised/80 select-none"
                      title="Click to reset row sorting"
                    >
                      <div className="flex items-center justify-between gap-1">
                        <span>#</span>
                        {sortCol && <span className="text-2xs text-accent">↺</span>}
                      </div>
                    </th>
                    {headers.map((h) => {
                      const isSorted = sortCol === h;
                      return (
                        <th
                          key={h}
                          onClick={() => handleHeaderClick(h)}
                          className={`p-2 border-r border-line/50 font-semibold whitespace-nowrap cursor-pointer hover:bg-raised/80 select-none transition-colors ${
                            isSorted ? "bg-accent/15 text-accent" : "hover:text-ink"
                          }`}
                          title={`Click to sort by ${h}`}
                        >
                          <div className="flex items-center justify-between gap-1.5">
                            <span>{h}</span>
                            <span className={`text-2xs font-bold ${isSorted ? "text-accent" : "text-ink-faint opacity-60"}`}>
                              {isSorted ? (sortDir === "asc" ? "▲" : "▼") : "↕"}
                            </span>
                          </div>
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/40 font-mono">
                  {sortedRows.map((r, i) => (
                    <tr
                      key={r.row_id}
                      ref={(el) => {
                        rawRowRefs.current[r.row_id] = el;
                      }}
                      className="hover:bg-raised/40 transition-colors h-9"
                    >
                      <td className="p-2 border-r border-line/50 text-ink-faint text-2xs whitespace-nowrap">{i + 1}</td>
                      {headers.map((h) => {
                        const cell = r.cells[h] ?? { raw_value: "", status: "clean" };
                        const isErr = cell.status === "error" || cell.status === "fixed";
                        const isFlashing = flashingCell?.row_id === r.row_id && flashingCell?.col === h;

                        return (
                          <td
                            key={h}
                            onClick={() => isErr && handleCellClick(r.row_id, h, cell)}
                            className={`p-2 border-r border-line/50 transition-all duration-300 whitespace-nowrap ${
                              isFlashing
                                ? "bg-red-500/40 text-red-100 font-bold ring-4 ring-red-400 animate-pulse shadow-xl shadow-red-500/50 z-10"
                                : isErr
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
              <span>Green cells = Transformed / Imputed {sortCol && <span className="ml-2 text-accent">(Sorted by {sortCol} {sortDir === "asc" ? "▲" : "▼"})</span>}</span>
            </div>
            <div
              ref={cleanContainerRef}
              onScroll={() => syncScroll("clean")}
              className="max-h-[600px] overflow-auto rounded-lg border border-line bg-panel scroll-smooth"
            >
              <table className="w-full text-left text-xs">
                <thead className="sticky top-0 bg-raised border-b border-line text-ink-dim font-mono z-20">
                  <tr>
                    <th
                      onClick={() => setSortCol(null)}
                      className="p-2 border-r border-line/50 whitespace-nowrap cursor-pointer hover:bg-raised/80 select-none"
                      title="Click to reset row sorting"
                    >
                      <div className="flex items-center justify-between gap-1">
                        <span>#</span>
                        {sortCol && <span className="text-2xs text-accent">↺</span>}
                      </div>
                    </th>
                    {headers.map((h) => {
                      const isSorted = sortCol === h;
                      return (
                        <th
                          key={h}
                          onClick={() => handleHeaderClick(h)}
                          className={`p-2 border-r border-line/50 font-semibold whitespace-nowrap cursor-pointer hover:bg-raised/80 select-none transition-colors ${
                            isSorted ? "bg-accent/15 text-accent" : "hover:text-ink"
                          }`}
                          title={`Click to sort by ${h}`}
                        >
                          <div className="flex items-center justify-between gap-1.5">
                            <span>{h}</span>
                            <span className={`text-2xs font-bold ${isSorted ? "text-accent" : "text-ink-faint opacity-60"}`}>
                              {isSorted ? (sortDir === "asc" ? "▲" : "▼") : "↕"}
                            </span>
                          </div>
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/40 font-mono">
                  {sortedRows.map((r, i) => (
                    <tr
                      key={r.row_id}
                      ref={(el) => {
                        cleanRowRefs.current[r.row_id] = el;
                      }}
                      className="hover:bg-raised/40 transition-colors h-9"
                    >
                      <td className="p-2 border-r border-line/50 text-ink-faint text-2xs whitespace-nowrap">{i + 1}</td>
                      {headers.map((h) => {
                        const cell = r.cells[h] ?? { clean_value: "", status: "clean" };
                        const isFixed = cell.status === "fixed" || cell.status === "error";
                        const isFlashing = flashingCell?.row_id === r.row_id && flashingCell?.col === h;

                        return (
                          <td
                            key={h}
                            onClick={() => isFixed && handleCellClick(r.row_id, h, cell)}
                            className={`p-2 border-r border-line/50 transition-all duration-300 whitespace-nowrap ${
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
