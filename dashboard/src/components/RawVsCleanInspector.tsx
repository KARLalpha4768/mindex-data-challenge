"use client";

import React from "react";
import { Badge } from "@/components/ui";
import type { Bundle, CsvDiffCell, CsvDiffRow, DatasetName } from "@/lib/types";
import {
  ESTIMATED_ROW_HEIGHT,
  FALLBACK_VIEWPORT_HEIGHT,
  OVERSCAN_ROWS,
  centeredScrollTop,
  compareRows,
  computeRowWindow,
  nextSortState,
  type RowWindow,
  type SortSource,
  type SortState,
} from "@/lib/tableWindow";

/* The diff shapes now live in `types.ts`, shared with the server-side loader
 * (`csvDiff.ts`) and the prompt builder (`grounding.ts`). They used to be
 * declared privately here; once the assistant began resolving a clicked cell out
 * of the same file, three private copies of one shape became three ways for the
 * two ends to stop agreeing about it. Aliased rather than renamed at every use
 * site so the diff stays readable. */
type CellInfo = CsvDiffCell;
type RowData = CsvDiffRow;

/* The sort comparator and the windowing arithmetic used to live in this file.
 * They now live in `@/lib/tableWindow` — pure, React-free, and covered by
 * `npm test`, which cannot render this component. Behaviour is unchanged; the
 * reasoning for each is written out there. */

/**
 * What this view reports upward when a cell is clicked.
 *
 * COORDINATES, not content — `dataset`, `rowIndex`, `column` are exactly what
 * travels to `/api/chat`, which then resolves the row itself out of the same
 * `csv_diff.json` this component fetched. The reasoning is written out in
 * `chatContract.ts` under `CellSelection`: anything the browser posts is
 * attacker-controlled text that would land inside the model's prompt, and cell
 * values are not worth opening that channel for when the server already holds
 * the file.
 *
 * `codes` is the exception that proves it: it is CLIENT-ONLY. `Dashboard` keeps
 * it out of the `viewContext` it posts and uses it for one thing — picking which
 * scripted answer the offline (no-API-key) path should give for this cell. It
 * never crosses the wire.
 */
export interface InspectorSelection {
  dataset: DatasetName;
  /** Index into the dataset's source `rows` array. Unique; `row_id` is not. */
  rowIndex: number;
  column: string;
  /** Defect codes recorded on this row. Client-only — see above. */
  codes: string[];
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
  /**
   * Report the dataset currently being inspected upward, so the grounded
   * assistant can be told which table is on screen.
   *
   * Optional, and the component behaves identically without it. The dataset
   * stays local state — it is not addressable and changes too often to belong
   * in the URL — but "which table am I looking at" is exactly what a question
   * like "why is this cell red?" depends on, and the alternative to reporting
   * it is the chat panel scraping the DOM for the highlighted button.
   */
  onDatasetChange?: (dataset: string) => void;
  /**
   * Report the clicked cell upward, so the grounded assistant can answer "why is
   * this cell red?" about the cell that is actually selected.
   *
   * `null` when the selection is cleared — closing the detail card, or switching
   * dataset, both of which must clear it: a coordinate that points into a table
   * nobody is looking at is worse than no coordinate at all.
   *
   * Optional, and this component behaves identically without it. Same contract
   * as `onDatasetChange` above and for the same reason: the alternative is the
   * chat panel reading this component's DOM for a highlighted cell, which would
   * make grounding depend on markup.
   */
  onCellChange?: (selection: InspectorSelection | null) => void;
  /**
   * Request that the shell open the grounded assistant panel.
   */
  onOpenAssistant?: () => void;
}

/**
 * A clickable column header.
 *
 * Cycles through ascending → descending → unsorted (see `nextSortState`).
 *
 * Rendered as a real `<button>` inside the `<th>` so it is keyboard reachable,
 * and carries `aria-sort` so the current state is announced rather than being
 * conveyed by a glyph alone. Both survive virtualization untouched: the header
 * is a `<thead>` of a real `<table>` and is never windowed.
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

/* ── The scroll window ─────────────────────────────────────────────────────
 *
 * Everything about WHY this exists is in `@/lib/tableWindow`. What lives here is
 * the part that needs React and the DOM: measuring the container, measuring one
 * real row, tracking the scroll offset, and driving the container to a given
 * row index.
 *
 * ROW HEIGHT IS MEASURED, NOT ASSUMED. The spacer heights are `index *
 * rowHeight`, so a rowHeight that is wrong by one pixel is wrong by 505 pixels
 * at the bottom of the transactions table — the scrollbar would stop short of
 * the last row, or overshoot it. A constant would have to be kept in sync with
 * Tailwind's padding scale, the font stack's line box, and the user's browser
 * zoom, and would be silently wrong on any of them. So a single probe row is
 * measured with `getBoundingClientRect()` (sub-pixel, unlike `offsetHeight`) and
 * that value drives the arithmetic.
 */

interface RowWindowHandle {
  /** Ref callback for the scroll container. */
  containerRef: (el: HTMLDivElement | null) => void;
  /** `onScroll` for the same container. */
  onScroll: (event: React.UIEvent<HTMLDivElement>) => void;
  /** The slice to mount and the spacer heights either side of it. */
  range: RowWindow;
  /** Bring a row index to the middle of this container. */
  scrollToIndex: (index: number) => void;
  /** Return to the top — used when the row set changes underneath. */
  scrollToTop: () => void;
}

function useRowWindow(rowCount: number): RowWindowHandle {
  const elRef = React.useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = React.useState(0);
  const [viewportHeight, setViewportHeight] = React.useState(FALLBACK_VIEWPORT_HEIGHT);
  const [rowHeight, setRowHeight] = React.useState(ESTIMATED_ROW_HEIGHT);

  /* Bumped by the ref callback so the measuring effect re-runs when the
   * container mounts or unmounts. The panes are conditionally rendered (the
   * split / raw-only / clean-only switch), so "the element exists" is not a
   * one-time event and a plain `[]` effect would measure a container that is
   * not there yet and never look again. */
  const [attachTick, setAttachTick] = React.useState(0);

  const containerRef = React.useCallback((el: HTMLDivElement | null) => {
    elRef.current = el;
    setAttachTick((tick) => tick + 1);
  }, []);

  const measure = React.useCallback(() => {
    const el = elRef.current;
    if (!el) return;
    setViewportHeight(el.clientHeight || FALLBACK_VIEWPORT_HEIGHT);

    const probe = el.querySelector<HTMLTableRowElement>("tr[data-diff-row]");
    const height = probe?.getBoundingClientRect().height ?? 0;
    // Guarded against re-render loops: only accept a genuinely different height.
    if (height > 1) setRowHeight((prev) => (Math.abs(prev - height) > 0.5 ? height : prev));
  }, []);

  React.useEffect(() => {
    const el = elRef.current;
    if (!el) return;
    measure();
    // A missing ResizeObserver (very old browsers, some test environments) costs
    // responsiveness to window resizes, not correctness — the fallback height
    // and the row-count effect below still produce a usable window.
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [attachTick, measure]);

  // The first rows only exist after the fetch resolves, so the probe above finds
  // nothing on mount. Re-measure whenever the row set changes size.
  React.useEffect(() => {
    measure();
  }, [rowCount, measure]);

  /**
   * Track the scroll offset — but only to row granularity.
   *
   * A raw `setScrollTop(e.currentTarget.scrollTop)` re-renders the pane on every
   * scroll event, which on a trackpad is 60-120 renders a second for a window
   * that has not changed. Collapsing to the row index means at most one render
   * per row scrolled past, and the rendered output is identical because that is
   * the only thing `computeRowWindow` reads the offset for.
   */
  const onScroll = React.useCallback(
    (event: React.UIEvent<HTMLDivElement>) => {
      const next = event.currentTarget.scrollTop;
      setScrollTop((prev) =>
        Math.floor(prev / rowHeight) === Math.floor(next / rowHeight) ? prev : next,
      );
    },
    [rowHeight],
  );

  const range = React.useMemo(
    () =>
      computeRowWindow({
        rowCount,
        scrollTop,
        viewportHeight,
        rowHeight,
        overscan: OVERSCAN_ROWS,
      }),
    [rowCount, scrollTop, viewportHeight, rowHeight],
  );

  const scrollToIndex = React.useCallback(
    (index: number) => {
      const el = elRef.current;
      if (!el || index < 0) return;
      const top = centeredScrollTop(index, rowCount, rowHeight, el.clientHeight || viewportHeight);
      /* Set the state FIRST, then animate. The target row is very likely outside
       * the mounted window, and `scrollTo` with smooth behaviour only reveals it
       * once the animation has run far enough to fire scroll events. Committing
       * the offset up front mounts the row on this render, so the flash
       * highlight is already painted by the time the scroll arrives — and it
       * still lands correctly if the animation is interrupted (the user grabs
       * the scrollbar) or suppressed (prefers-reduced-motion). */
      setScrollTop(top);
      el.scrollTo({ top, behavior: "smooth" });
    },
    [rowCount, rowHeight, viewportHeight],
  );

  const scrollToTop = React.useCallback(() => {
    setScrollTop(0);
    elRef.current?.scrollTo({ top: 0, behavior: "auto" });
  }, []);

  return React.useMemo(
    () => ({ containerRef, onScroll, range, scrollToIndex, scrollToTop }),
    [containerRef, onScroll, range, scrollToIndex, scrollToTop],
  );
}

/* ── One pane ──────────────────────────────────────────────────────────────
 *
 * Presentational and deliberately hook-free. The two panes differ in four
 * things — which value they show, how status maps to colour, whether the flash
 * highlight applies, and whether rows register themselves in a ref map — and
 * every one of those is a prop. Keeping it hook-free means mounting and
 * unmounting it with the view-mode switch cannot affect hook order anywhere.
 */

interface PaneProps {
  side: SortSource;
  headers: string[];
  rows: KeyedRow[];
  range: RowWindow;
  sort: SortState | null;
  onSort: (col: string, source: SortSource) => void;
  containerRef: (el: HTMLDivElement | null) => void;
  onScroll: (event: React.UIEvent<HTMLDivElement>) => void;
  onCellClick: (row: KeyedRow, col: string, info: CellInfo) => void;
  /** Clean side only: the ref map click-to-scroll consults first. */
  rowRefs?: React.MutableRefObject<Record<string, HTMLTableRowElement | null>>;
  flashingCell: { uid: string; col: string } | null;
  activeCell: { uid: string; col: string } | null;
  /** Raw side only: the "try this" example cell, outlined until it is used. */
  spotlight: { uid: string; col: string } | null;
  label: string;
  legend: React.ReactNode;
}

function DiffPane({
  side,
  headers,
  rows,
  range,
  sort,
  onSort,
  containerRef,
  onScroll,
  onCellClick,
  rowRefs,
  flashingCell,
  activeCell,
  spotlight,
  label,
  legend,
}: PaneProps) {
  const isRaw = side === "raw";
  const columnCount = headers.length + 1; // + the row-number column

  return (
    <div className="space-y-2">
      <div
        className={`flex flex-wrap items-center justify-between gap-2 text-xs font-semibold ${
          isRaw ? "text-red-400" : "text-green-400"
        }`}
      >
        <span className="font-bold">{label}</span>
        <div>{legend}</div>
      </div>
      <div
        ref={containerRef}
        onScroll={onScroll}
        tabIndex={0}
        role="region"
        aria-label={label}
        className="max-h-[600px] overflow-auto rounded-lg border border-line bg-panel"
      >
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 z-20 bg-raised border-b border-line text-ink-dim font-mono">
            <tr>
              <th className="p-2 border-r border-line/50" scope="col">
                #
              </th>
              {headers.map((h) => (
                <SortableHeader key={h} label={h} source={side} sort={sort} onSort={onSort} />
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-line/40 font-mono">
            {/* Spacer for the rows above the window. Inline height (and an
                explicit zero border) because Tailwind's `divide-y` targets
                every child of the tbody and would otherwise add a visible rule
                across the empty space. */}
            {range.padTop > 0 && (
              <tr aria-hidden="true" style={{ height: range.padTop, borderTopWidth: 0 }}>
                <td colSpan={columnCount} className="p-0" />
              </tr>
            )}

            {rows.slice(range.start, range.end).map((r, offset) => {
              const displayIndex = range.start + offset;
              return (
                <tr
                  key={r.uid}
                  // The probe `useRowWindow` measures. One attribute, on every
                  // row, so it does not matter which one is on screen.
                  data-diff-row=""
                  ref={
                    rowRefs
                      ? (el) => {
                          /* Keyed by `uid`, and React sets this to `null` when
                           * the row scrolls out of the window — which is exactly
                           * what click-to-scroll needs, because a null here is
                           * the signal to fall back to the index-based path
                           * rather than to scroll a detached node. */
                          rowRefs.current[r.uid] = el;
                        }
                      : undefined
                  }
                  className="hover:bg-raised/40 transition-colors"
                >
                  <td
                    className="p-2 border-r border-line/50 text-ink-faint text-2xs"
                    title={`source row ${Number(r.uid) + 1}`}
                  >
                    {displayIndex + 1}
                  </td>
                  {headers.map((h) => {
                    const cell = r.cells[h] ?? {
                      raw_value: "",
                      clean_value: "",
                      status: "clean" as const,
                      defect_code: null,
                      explanation: null,
                    };
                    // Every flagged cell is inspectable, but only a genuine
                    // exclusion is painted red. A preserved finding gets its own
                    // colour so the raw pane does not accuse the pipeline of
                    // dropping data it deliberately kept.
                    const isPreserved = cell.status === "preserved";
                    const isFlagged =
                      cell.status === "error" || cell.status === "fixed" || isPreserved;
                    const isFlashing =
                      !isRaw && flashingCell?.uid === r.uid && flashingCell?.col === h;
                    const isActive = activeCell?.uid === r.uid && activeCell?.col === h;
                    const isSpotlit = isRaw && spotlight?.uid === r.uid && spotlight?.col === h;

                    const tone = isFlashing
                      ? "bg-green-500/40 text-green-100 font-bold ring-2 ring-green-400 ring-inset"
                      : isPreserved
                        ? "cursor-pointer bg-amber-500/15 text-amber-300 font-semibold hover:bg-amber-500/25"
                        : isFlagged
                          ? isRaw
                            ? "cursor-pointer bg-red-500/15 text-red-300 font-semibold hover:bg-red-500/25"
                            : "cursor-pointer bg-green-500/15 text-green-300 font-semibold hover:bg-green-500/25"
                          : "text-ink-dim";

                    const outline = isActive
                      ? " ring-1 ring-accent ring-inset"
                      : isSpotlit
                        ? " ring-1 ring-accent/70 ring-inset"
                        : "";

                    return (
                      <td
                        key={h}
                        onClick={() => isFlagged && onCellClick(r, h, cell as CellInfo)}
                        // `whitespace-nowrap` is load-bearing, not cosmetic: the
                        // window arithmetic assumes every row is the same
                        // height, and a wrapped cell would make one row taller
                        // than the spacers account for.
                        className={`whitespace-nowrap p-2 border-r border-line/50 transition-colors ${tone}${outline}`}
                        title={
                          isPreserved
                            ? `${cell.defect_code} — flagged and deliberately preserved, not an error`
                            : isFlagged
                              ? isRaw
                                ? `Click to inspect ${cell.defect_code}`
                                : "Click to inspect fix"
                              : undefined
                        }
                      >
                        {isRaw ? cell.raw_value : cell.clean_value}
                        {isRaw && cell.defect_code && (
                          <span className="ml-1 rounded bg-red-500/30 px-1 py-0.5 text-2xs font-bold text-red-200">
                            {cell.defect_code}
                          </span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              );
            })}

            {/* Spacer for the rows below the window. */}
            {range.padBottom > 0 && (
              <tr aria-hidden="true" style={{ height: range.padBottom, borderTopWidth: 0 }}>
                <td colSpan={columnCount} className="p-0" />
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function RawVsCleanInspector({
  bundle,
  onSelectDefect,
  onDatasetChange,
  onCellChange,
  onOpenAssistant,
}: Props) {
  const [data, setData] = React.useState<CsvDiffData | null>(null);
  const [dataset, setDataset] = React.useState<"transactions" | "products" | "stores">("transactions");
  const [viewMode, setViewMode] = React.useState<"split" | "raw" | "clean">("split");
  const [selectedCode, setSelectedCode] = React.useState<string>("all");
  /**
   * The clicked cell.
   *
   * `rowIndex` (the row's position in the SOURCE array, i.e. `uid`) is carried
   * alongside `row_id` because the two are not interchangeable: `row_id` is the
   * transaction id and the 15 TX-09 rows share one, so it cannot address a row.
   * `rowIndex` is what the assistant is told, and what the server looks up.
   */
  const [activeCell, setActiveCell] = React.useState<{
    /**
     * The dataset this coordinate belongs to, captured at click time.
     *
     * Carried on the selection rather than read from `dataset` when reporting,
     * because the two can disagree for exactly one render: switching dataset
     * changes `dataset` immediately and clears `activeCell` in an effect, so a
     * reporter reading both would emit `(new dataset, old row index)` once — a
     * coordinate that is well-formed, resolvable, and points at the wrong row.
     */
    dataset: DatasetName;
    uid: string;
    rowIndex: number;
    row_id: string;
    col: string;
    info: CellInfo;
    /** Defect codes on the whole row, for the offline answer picker. */
    codes: string[];
  } | null>(null);

  const [sort, setSort] = React.useState<SortState | null>(null);
  // Keyed by `uid`, not `row_id` — see the keyedRows comment. Two duplicate
  // rows must be able to flash independently.
  const [flashingCell, setFlashingCell] = React.useState<{ uid: string; col: string } | null>(null);
  const flashTimerRef = React.useRef<NodeJS.Timeout | null>(null);
  const cleanRowRefs = React.useRef<Record<string, HTMLTableRowElement | null>>({});

  /* ── Derived rows ────────────────────────────────────────────────────────
   *
   * EVERY HOOK IN THIS COMPONENT SITS ABOVE THE SINGLE EARLY RETURN further
   * down. React requires the same hooks in the same order on every render; one
   * placed after a conditional `return` runs on some renders and not others,
   * which is React error #310 — a blank page reading "Application error: a
   * client-side exception has occurred". That has taken this deployed site down
   * twice. Hence the `?? []` defaults everywhere below: the hooks always run,
   * and simply have nothing to work with until the fetch resolves.
   *
   * These are memoised rather than computed inline (as filtering and sorting
   * previously were) because they now feed the window arithmetic, which runs on
   * every scroll event. Re-sorting 505 rows while someone drags a scrollbar is
   * the second-worst thing this view could do.
   */

  const currentDataset = data?.[dataset];
  const headers = React.useMemo(() => currentDataset?.headers ?? [], [currentDataset]);

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
   * assigned once, and unchanged by sorting, filtering or windowing. It also
   * fixes the clean-row ref map, which was keyed by `row_id` and so collided for
   * those same 15 rows, sending scroll-to-row to whichever duplicate registered
   * last. Virtualization makes it matter more, not less: with rows mounting and
   * unmounting as you scroll, a non-unique key is a guarantee of node reuse
   * across genuinely different rows.
   */
  const keyedRows: KeyedRow[] = React.useMemo(
    () => (currentDataset?.rows ?? []).map((r, sourceIndex) => ({ ...r, uid: `${sourceIndex}` })),
    [currentDataset],
  );

  const filteredRows = React.useMemo(
    () =>
      selectedCode === "all"
        ? keyedRows
        : keyedRows.filter((r) => r.defects.includes(selectedCode)),
    [keyedRows, selectedCode],
  );

  // One ordering, applied to both panes. `slice()` first because `sort` mutates
  // in place and `filteredRows` is derived from the loaded dataset — sorting it
  // directly would permanently scramble the source order this view lets you
  // return to.
  const displayRows = React.useMemo(
    () => (sort ? filteredRows.slice().sort((a, b) => compareRows(a, b, sort)) : filteredRows),
    [filteredRows, sort],
  );

  /**
   * uid -> position in `displayRows`.
   *
   * This is what makes click-to-scroll work under virtualization. The clean-side
   * row for a clicked cell may not be mounted at all, so there is no element to
   * call `scrollIntoView` on; what there always is, is the row's index in the
   * order currently on screen, which is all `scrollToIndex` needs. Rebuilt only
   * when the order changes, not on every scroll.
   */
  const displayIndexByUid = React.useMemo(() => {
    const map = new Map<string, number>();
    displayRows.forEach((r, index) => map.set(r.uid, index));
    return map;
  }, [displayRows]);

  const allCodes = React.useMemo(
    () => Array.from(new Set(keyedRows.flatMap((r) => r.defects))).sort(),
    [keyedRows],
  );

  /**
   * The worked example the "try this" affordance points at.
   *
   * DERIVED, NEVER HARDCODED. A literal row number would be correct until the
   * pipeline is re-run with a different seed, and then it would silently point
   * at an ordinary row — the dashboard would be demonstrating its own stale-
   * figure failure mode on its own front page.
   *
   * The preference order is a judgement about what is worth a reviewer's first
   * click: TX-03 (a silent discount, where the reported total is PRESERVED
   * rather than recomputed) and TX-10 (a return, whose negative quantity is
   * correct data) are the two cases in this dataset where the obvious handling
   * is the wrong handling. Both are `preserved`, so the example also explains
   * the amber colour, which is the one legend entry a reviewer cannot guess.
   * Falls through to any preserved cell, so `products` and `stores` get an
   * example too, and to `null`, which simply hides the affordance.
   */
  const exampleCell = React.useMemo(() => {
    const pick = (match: (cell: CsvDiffCell) => boolean) => {
      for (const row of keyedRows) {
        for (const col of headers) {
          const cell = row.cells[col];
          if (cell && match(cell)) return { row, col, cell };
        }
      }
      return null;
    };
    return (
      pick((c) => c.defect_code === "TX-03" && c.status === "preserved") ??
      pick((c) => c.defect_code === "TX-10" && c.status === "preserved") ??
      pick((c) => c.status === "preserved")
    );
  }, [keyedRows, headers]);

  /* One window per pane. Called unconditionally and in a fixed order; the panes
   * themselves are conditionally rendered, but these hooks are not. */
  const rawWindow = useRowWindow(displayRows.length);
  const cleanWindow = useRowWindow(displayRows.length);

  /** Cycle a column: ascending → descending → back to source (file) order. */
  const handleSort = React.useCallback((col: string, source: SortSource) => {
    setSort((current) => nextSortState(current, col, source));
  }, []);

  const handleCellClick = React.useCallback(
    (row: KeyedRow, col: string, info: CellInfo) => {
      setActiveCell({
        dataset,
        uid: row.uid,
        rowIndex: Number(row.uid),
        row_id: row.row_id,
        col,
        info,
        codes: row.defects ?? [],
      });

      // Flash the counterpart cell for 15 seconds. This is state, not a DOM
      // mutation, which is why it survives windowing untouched: whenever the row
      // mounts — now, or in a moment when the scroll below reaches it — it
      // paints highlighted.
      setFlashingCell({ uid: row.uid, col });
      if (flashTimerRef.current) clearTimeout(flashTimerRef.current);
      flashTimerRef.current = setTimeout(() => setFlashingCell(null), 15000);

      /* CLICK-TO-SCROLL, UNDER VIRTUALIZATION.
       *
       * Bring the corresponding clean-side row into view. Two paths, in this
       * order, and the second one is the whole reason this needed changing:
       *
       *   1. The row is mounted (it is inside the window or its overscan) —
       *      `scrollIntoView` on the real node, exactly as before. Pixel-exact,
       *      and it also nudges the page if the pane itself is off-screen.
       *
       *   2. The row is NOT mounted, which is now the common case: 505 rows,
       *      ~34 in the DOM. There is no node, and `cleanRowRefs.current[uid]`
       *      is null (React nulls it on unmount). Scroll the container to the
       *      row's INDEX instead — the index is known from `displayIndexByUid`
       *      whatever the sort and filter are — which mounts the row, and the
       *      flash state above then paints it.
       *
       * The ref map is still keyed by `uid` and is still the fast path. What
       * changed is that a null lookup is no longer a dead end.
       */
      const mounted = cleanRowRefs.current[row.uid];
      if (mounted) {
        mounted.scrollIntoView({ behavior: "smooth", block: "center" });
        return;
      }
      const index = displayIndexByUid.get(row.uid);
      if (index !== undefined) cleanWindow.scrollToIndex(index);
    },
    [dataset, displayIndexByUid, cleanWindow],
  );

  /**
   * The "try this" affordance: select the worked example and open the assistant.
   *
   * One click has to do the entire mechanism, because the mechanism is not
   * guessable — nothing on screen says that clicking a cell is what makes the
   * assistant cell-aware. So this selects the cell (detail card, flash,
   * scroll-to-row, and the coordinate reported upward through `onCellChange`)
   * and then opens the panel, which by then already has the selection.
   */
  const showExample = React.useCallback(() => {
    if (!exampleCell) return;
    handleCellClick(exampleCell.row, exampleCell.col, exampleCell.cell);
    rawWindow.scrollToIndex(displayIndexByUid.get(exampleCell.row.uid) ?? 0);
    onOpenAssistant?.();
  }, [exampleCell, handleCellClick, rawWindow, displayIndexByUid, onOpenAssistant]);

  React.useEffect(() => {
    return () => {
      if (flashTimerRef.current) clearTimeout(flashTimerRef.current);
    };
  }, []);

  // Changing dataset changes the columns, so a sort keyed to a column that no
  // longer exists would silently do nothing. Reset rather than leave a stale
  // indicator pointing at a header that is gone.
  //
  // The selected cell goes with it, and that one is not cosmetic: a selection is
  // `(dataset, rowIndex, column)`, so keeping it across a dataset switch would
  // leave the detail card describing a row of the previous table and would send
  // the assistant a coordinate that now addresses a different row entirely.
  //
  // Both panes also go back to the top. Under virtualization a retained scroll
  // offset is worse than it looks: 500 rows into `transactions` is past the end
  // of `stores`, and the window would clamp to the last 16 rows of a table the
  // reviewer has only just opened.
  React.useEffect(() => {
    setSort(null);
    setActiveCell(null);
    rawWindow.scrollToTop();
    cleanWindow.scrollToTop();
    // `rawWindow`/`cleanWindow` are intentionally not dependencies: this must
    // run when the DATASET changes and only then. Their `scrollToTop` callbacks
    // are stable (`useCallback` with no deps), so nothing is stale.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataset]);

  /**
   * Tell the shell which cell is selected, as coordinates.
   *
   * HOOK ORDER: with the other effects, ABOVE the `if (!currentDataset) return`
   * below. Every hook in this component must stay above that early return — a
   * hook below it runs on some renders and not others, which is React error #310
   * and has twice taken the deployed site down.
   *
   * `dataset` is a dependency as well as `activeCell` because the selection is
   * only meaningful as a pair, and the mismatch between them — for the one
   * render between "dataset changed" and "the effect above cleared the cell" —
   * is reported as NO selection rather than as a coordinate that resolves to the
   * wrong row of the new table.
   */
  React.useEffect(() => {
    if (!onCellChange) return;
    const current = activeCell && activeCell.dataset === dataset ? activeCell : null;
    onCellChange(
      current
        ? {
            dataset: current.dataset,
            rowIndex: current.rowIndex,
            column: current.col,
            codes: current.codes,
          }
        : null,
    );
  }, [activeCell, dataset, onCellChange]);

  /**
   * Tell the shell which dataset is on screen, including on first mount so the
   * assistant is correct before anything is clicked.
   *
   * HOOK ORDER: this sits with the other effects, ABOVE the `if
   * (!currentDataset) return` further down. Every hook in this component must
   * stay above that early return — a hook below it runs on some renders and not
   * others, which is React error #310 and takes the deployed page down with a
   * client-side exception. The callback is memoised by the parent, so this fires
   * only when the dataset actually changes.
   */
  React.useEffect(() => {
    onDatasetChange?.(dataset);
  }, [dataset, onDatasetChange]);

  React.useEffect(() => {
    fetch("/data/csv_diff.json")
      .then((r) => r.json())
      .then((d) => setData(d))
      .catch(() => console.error("Failed to load csv_diff.json"));
  }, []);

  /* ── END OF HOOKS. Nothing below this line may call one. ───────────────── */

  if (!currentDataset) {
    return (
      <div className="rounded-lg border border-line bg-panel p-8 text-center text-ink-dim">
        Loading CSV visual diff inspection data...
      </div>
    );
  }

  const activeKey = activeCell ? { uid: activeCell.uid, col: activeCell.col } : null;
  // The spotlight is a hint, so it retires the moment it has served its purpose:
  // once anything is selected, the outline would just be a second highlight
  // competing with the real one.
  const spotlight =
    !activeCell && exampleCell && displayIndexByUid.has(exampleCell.row.uid)
      ? { uid: exampleCell.row.uid, col: exampleCell.col }
      : null;

  return (
    <div className="space-y-6">
      {/* Header Bar */}
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-line bg-panel p-4">
        <div>
          <h2 className="text-lg font-bold text-ink">Raw vs. Clean CSV Visual Inspector</h2>
          <p className="text-xs text-ink-dim">
            Raw source cells with seeded defects are marked in{" "}
            <span className="font-semibold text-red-400">red</span>, deliberate
            non-corrections in <span className="font-semibold text-amber-300">amber</span>, and
            pipeline transformations in <span className="font-semibold text-green-400">green</span>.
            Both panes hold {displayRows.length} rows; only the visible rows are in the DOM.
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
                aria-pressed={dataset === ds}
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
                aria-pressed={viewMode === mode}
                className={`rounded px-2.5 py-1 text-xs font-semibold capitalize transition-colors ${
                  viewMode === mode ? "bg-accent text-accent-contrast" : "text-ink-dim hover:text-ink"
                }`}
              >
                {mode === "split" ? "Split View" : mode === "raw" ? "Raw Only" : "Clean Only"}
              </button>
            ))}
          </div>

          {/* Defect Code Filter */}
          <label className="sr-only" htmlFor="defect-filter">
            Filter rows by defect code
          </label>
          <select
            id="defect-filter"
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

      {/* Prominent Reviewer Guidance Banner */}
      <div className="rounded-lg border border-accent/40 bg-gradient-to-r from-accent/10 via-panel to-panel p-4 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line/60 pb-2 mb-3">
          <div className="flex items-center gap-2">
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-accent text-2xs font-bold text-accent-contrast">
              i
            </span>
            <h3 className="text-xs font-bold uppercase tracking-wider text-ink">
              Reviewer Inspection Guide — 3-Step Evaluation Loop
            </h3>
          </div>
          <span className="text-2xs font-mono text-ink-faint">
            Visual Diff · Grounded AI · Source Tracing
          </span>
        </div>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-3 text-xs">
          {/* Step 1 */}
          <div className="rounded border border-line/70 bg-panel/70 p-3 space-y-1">
            <div className="flex items-center gap-2">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-red-500/20 text-red-400 font-bold font-mono text-xs">
                1
              </span>
              <span className="font-semibold text-ink">Click on Seeded Defect (Red Cell)</span>
            </div>
            <p className="text-2xs text-ink-dim leading-relaxed">
              Click any <span className="font-semibold text-red-400">red cell</span> (seeded defect) or <span className="font-semibold text-amber-300">amber cell</span> (preserved non-correction) in the Raw CSV.
            </p>
          </div>

          {/* Step 2 */}
          <div className="rounded border border-line/70 bg-panel/70 p-3 space-y-1">
            <div className="flex items-center gap-2">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-accent/20 text-accent font-bold font-mono text-xs">
                2
              </span>
              <span className="font-semibold text-ink">Ask Chatbot (Simple + Extended)</span>
            </div>
            <p className="text-2xs text-ink-dim leading-relaxed">
              Open the AI Assistant. It reads server-side row diffs and gives an <span className="text-accent font-medium">Executive TL;DR</span> plus an <span className="text-accent font-medium">Extended Deep Analysis</span>.
            </p>
          </div>

          {/* Step 3 */}
          <div className="rounded border border-line/70 bg-panel/70 p-3 space-y-1">
            <div className="flex items-center gap-2">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-accent text-accent-contrast font-bold font-mono text-xs">
                3
              </span>
              <span className="font-semibold text-ink">Defect Explorer & Linked Code</span>
            </div>
            <p className="text-2xs text-ink-dim leading-relaxed">
              Jump directly to the defect dossier to inspect audit records, lineage stages, and <span className="text-ink font-medium">exact linked Python/SQL source lines</span>.
            </p>
          </div>
        </div>
      </div>

      {/* ── The worked example ───────────────────────────────────────────────
          The cell → assistant link is the most interesting thing this view can
          do and the least discoverable: nothing on screen suggests that clicking
          a cell is what makes the assistant cell-aware. So the view offers one
          concrete cell, says why that cell is worth looking at, and does the
          whole gesture on a single click. Retired as soon as a cell is
          selected — by then the mechanism has demonstrated itself. */}
      {!activeCell && exampleCell && (
        <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-accent/30 bg-accent/5 px-4 py-3">
          <div className="max-w-3xl space-y-1">
            <p className="text-xs font-medium uppercase tracking-wider text-ink-faint">
              Try Worked Example
            </p>
            <p className="text-sm text-ink-dim">
              Source row {Number(exampleCell.row.uid) + 1},{" "}
              <span className="font-mono text-ink">{exampleCell.col}</span> is marked{" "}
              <span className="font-mono text-amber-300">{exampleCell.cell.defect_code}</span> —
              raw <span className="font-mono text-ink">{exampleCell.cell.raw_value || "(empty)"}</span>,
              cleaned{" "}
              <span className="font-mono text-ink">{exampleCell.cell.clean_value || "(empty)"}</span>.
              The pipeline flagged it and deliberately did not change it, which is the case where
              the obvious handling is the wrong one. Selecting it also hands the coordinate to the
              assistant.
            </p>
          </div>
          <button
            type="button"
            onClick={showExample}
            className="shrink-0 rounded border border-accent bg-accent/15 px-3 py-1.5 text-xs font-semibold text-accent transition-colors hover:bg-accent/25"
          >
            Select this cell & test Step 2 / Step 3
          </button>
        </div>
      )}

      {/* Popover Detail Modal / Card when cell is clicked */}
      {activeCell && (
        <div className="space-y-3 rounded-lg border border-accent/40 bg-raised p-4 shadow-md">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Badge tone={activeCell.info.status === "error" ? "bad" : "accent"}>
                {activeCell.info.defect_code ?? "INFO"}
              </Badge>
              <span className="font-mono text-xs font-bold text-ink">
                Row: {activeCell.row_id} · Column: {activeCell.col}
              </span>
              {/* The source-file row number. `row_id` is not unique (the 15
                  TX-09 rows share one), so this is the only thing that
                  identifies WHICH row — and it is exactly the coordinate the
                  assistant is given, so the two can be checked against each
                  other on screen. */}
              <span className="font-mono text-2xs text-ink-faint">
                source row {activeCell.rowIndex + 1}
              </span>
            </div>
            <button
              type="button"
              onClick={() => setActiveCell(null)}
              className="rounded border border-line bg-panel px-2 py-0.5 text-xs text-ink-dim hover:text-ink hover:border-ink-faint"
            >
              Close
            </button>
          </div>

          {/* The transformation, stated once. Shown while the counterpart cell
              is still highlighted in the clean pane, so the card and the table
              are talking about the same thing. */}
          {flashingCell && (
            <div className="flex flex-wrap items-center gap-2 rounded border border-line bg-panel px-3 py-2 font-mono text-xs">
              <span className="text-2xs uppercase tracking-wider text-ink-faint">
                highlighted in the clean pane for 15s
              </span>
              <span className="text-ink-faint">·</span>
              <span className="text-red-300">{activeCell.info.raw_value || "(empty)"}</span>
              <span className="text-ink-faint">→</span>
              <span className="text-green-300">{activeCell.info.clean_value || "(empty)"}</span>
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

          <p className="mt-3 text-xs leading-relaxed text-ink-dim">
            {activeCell.info.explanation || "Standardized by cleaning rules."}
          </p>

          {/* Discoverability */}
          {onCellChange && (
            <p className="mt-2 rounded border border-accent/30 bg-accent/5 px-2.5 py-1.5 text-2xs text-ink-dim">
              <span className="font-semibold text-accent">Grounded Context Connected:</span> Coordinates ({activeCell.dataset}, row {activeCell.rowIndex + 1},{" "}
              {activeCell.col}) are active. Open the Assistant below to receive both a simple executive summary and extended deep technical analysis.
            </p>
          )}

          <div className="mt-3 flex flex-wrap items-center justify-end gap-2 pt-2 border-t border-line/50">
            {onOpenAssistant && (
              <button
                type="button"
                onClick={onOpenAssistant}
                className="flex items-center gap-1.5 rounded border border-accent bg-accent/15 px-3 py-1.5 text-xs font-semibold text-accent transition-colors hover:bg-accent/25"
              >
                <span>Step 2:</span>
                <span>Ask Chatbot (Simple + Extended)</span>
              </button>
            )}
            {activeCell.info.defect_code && onSelectDefect && (
              <button
                type="button"
                onClick={() => onSelectDefect(activeCell.info.defect_code!)}
                className="flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-xs font-semibold text-accent-contrast hover:bg-accent/90"
              >
                <span>Step 3:</span>
                <span>View {activeCell.info.defect_code} in Defect Explorer & Linked Code →</span>
              </button>
            )}
          </div>
        </div>
      )}

      {/* Grids Container */}
      <div className={`grid gap-6 ${viewMode === "split" ? "grid-cols-1 lg:grid-cols-2" : "grid-cols-1"}`}>
        {(viewMode === "split" || viewMode === "raw") && (
          <DiffPane
            side="raw"
            headers={headers}
            rows={displayRows}
            range={rawWindow.range}
            sort={sort}
            onSort={handleSort}
            containerRef={rawWindow.containerRef}
            onScroll={rawWindow.onScroll}
            onCellClick={handleCellClick}
            flashingCell={null}
            activeCell={activeKey}
            spotlight={spotlight}
            label={`Original Raw CSV (${dataset}.csv)`}
            legend={
              <span className="inline-flex items-center gap-1.5 rounded-full border border-red-500/40 bg-red-500/15 px-2.5 py-0.5 text-2xs text-red-300 font-semibold shadow-sm">
                <span className="h-1.5 w-1.5 rounded-full bg-red-400 animate-pulse" />
                <span>Click on seeded defect (red cell) → Ask AI Assistant</span>
              </span>
            }
          />
        )}

        {(viewMode === "split" || viewMode === "clean") && (
          <DiffPane
            side="clean"
            headers={headers}
            rows={displayRows}
            range={cleanWindow.range}
            sort={sort}
            onSort={handleSort}
            containerRef={cleanWindow.containerRef}
            onScroll={cleanWindow.onScroll}
            onCellClick={handleCellClick}
            rowRefs={cleanRowRefs}
            flashingCell={flashingCell}
            activeCell={activeKey}
            spotlight={null}
            label={`Cleaned Pipeline Output (${dataset}_clean.csv)`}
            legend={
              <span className="inline-flex items-center gap-1.5 rounded-full border border-green-500/40 bg-green-500/15 px-2.5 py-0.5 text-2xs text-green-300 font-semibold shadow-sm">
                <span className="h-1.5 w-1.5 rounded-full bg-green-400" />
                <span>Green cells = Transformed / Imputed Output</span>
              </span>
            }
          />
        )}
      </div>
    </div>
  );
}
