/**
 * The two pieces of the Raw vs Clean inspector that are load-bearing, pure, and
 * testable without a DOM: the type-aware sort comparator, and the windowing
 * arithmetic that keeps 505 rows x 8 columns x 2 panes from freezing a browser.
 *
 * WHY THIS FILE EXISTS AT ALL
 * ---------------------------
 * `RawVsCleanInspector.tsx` is a `"use client"` component that fetches a ~1 MB
 * JSON file on mount. Nothing in this repository's test suite can render it —
 * the suite is `tsc` plus plain `node`, with no jsdom and no test runner, which
 * is the right trade for a review artefact but means every line inside that
 * component is verified only by a human clicking. Both the comparator and the
 * window maths are exactly the kind of code where an off-by-one is invisible
 * on screen and catastrophic in behaviour (a comparator that silently ignores
 * `$` sorts money alphabetically; a window that is one row short leaves a gap
 * that looks like missing data). Pulled out here, both are covered by
 * `npm test` and the component keeps only the parts that genuinely need React.
 *
 * Nothing in this module imports React or touches `document`. That is a
 * deliberate constraint, not an accident: it is what lets the test build
 * (`tests/tsconfig.json`, which compiles `src/lib/**` to CommonJS) include it.
 */

import type { CsvDiffRow } from "./types";

/* ── Sorting ────────────────────────────────────────────────────────────────
 *
 * WHY ONE SORT STATE FOR TWO TABLES: the panes are side by side and their rows
 * correspond one-to-one. Sorting them independently would silently destroy that
 * correspondence — row 4 on the left would stop being row 4 on the right — and
 * would also break click-to-scroll, which looks up a clean-side row and brings
 * it into view. So a click on either header reorders BOTH tables identically.
 *
 * WHICH VALUE IS SORTED ON: the one in the pane you clicked. Clicking a header
 * on the raw side orders by the raw strings, which is what someone auditing the
 * source file expects; clicking on the clean side orders by the cleaned values.
 * Both produce the same row order across the two tables.
 */

export type SortDirection = "asc" | "desc";
export type SortSource = "raw" | "clean";

export interface SortState {
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
export function comparableValue(raw: string): number | string | null {
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

export function compareRows(a: CsvDiffRow, b: CsvDiffRow, sort: SortState): number {
  const pick = (row: CsvDiffRow) => {
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
 * The header-click cycle: ascending -> descending -> back to source order.
 *
 * The third state matters here: the source order IS the file order, which is
 * information — row 1 is the first line of the CSV — so there has to be a way
 * back to it without a reload. `null` means "unsorted".
 *
 * Clicking a *different* column, or the same column in the other pane, starts
 * that column's own cycle at ascending rather than inheriting the previous
 * direction; inheriting it makes the first click on a new column look like it
 * did nothing whenever the previous column happened to be descending.
 */
export function nextSortState(
  current: SortState | null,
  col: string,
  source: SortSource,
): SortState | null {
  if (!current || current.col !== col || current.source !== source) {
    return { col, source, dir: "asc" };
  }
  if (current.dir === "asc") return { col, source, dir: "desc" };
  return null;
}

/* ── Windowing ──────────────────────────────────────────────────────────────
 *
 * THE PROBLEM THIS SOLVES, measured rather than guessed: the transactions diff
 * is 505 rows and 8 columns, rendered into TWO side-by-side tables, each with a
 * leading row-number column. That is 505 x 9 x 2 = 9,090 `<td>` elements, every
 * one of them carrying a click handler and a conditionally-computed className.
 * On the deployed build this blocked the main thread hard enough that
 * screenshotting the page failed with "the page is busy" and script evaluation
 * timed out after 45 seconds. Sorting made it worse: every header click rebuilt
 * all 9,090 cells.
 *
 * THE APPROACH: render only the rows that can be seen, plus an overscan margin,
 * and replace everything above and below with two spacer `<tr>` elements whose
 * heights add up to exactly the space the omitted rows would have occupied. The
 * scrollbar therefore behaves exactly as it did before — same total height,
 * same thumb size, same position — while the DOM holds ~34 rows instead of 505.
 *
 * WHY SPACER ROWS RATHER THAN ABSOLUTE POSITIONING: a table is not a list. The
 * column widths of this table are computed by the browser from the content of
 * the rows that are present, and the header is `position: sticky` inside the
 * scroll container. Absolutely positioning rows takes them out of the table's
 * layout, which breaks both. Two zero-content spacer rows keep the element a
 * real `<table>`: `<thead>` still sticks, `aria-sort` still applies, column
 * widths still resolve, and keyboard scrolling of the container is unchanged.
 *
 * WHY NO LIBRARY: this is forty lines of arithmetic with an exact test. The
 * smallest well-maintained alternative (`@tanstack/react-virtual`) is ~10 KB
 * gzipped and would be the only runtime dependency in this dashboard that is
 * not React, Next, recharts or the syntax highlighter — for a table that has
 * one uniform row height and needs no dynamic measurement beyond a single probe.
 */

/**
 * Fallback row height in CSS pixels, used for the very first render before a
 * real row exists to measure. The rows are one line of `text-xs` monospace with
 * `py-2`, which lands at ~33-34px; being a pixel out for one frame is
 * invisible, and the component replaces this with the measured height as soon
 * as a row is on screen. See `useRowWindow` in `RawVsCleanInspector.tsx`.
 */
export const ESTIMATED_ROW_HEIGHT = 34;

/**
 * Fallback viewport height, matching the `max-h-[600px]` on the scroll
 * containers. Only used before the container has been measured.
 */
export const FALLBACK_VIEWPORT_HEIGHT = 600;

/**
 * Rows rendered beyond each edge of the visible area.
 *
 * Eight is chosen against the failure mode, not against a benchmark: the thing
 * that must never happen is a fast flick of the wheel exposing blank space
 * before React catches up. Eight rows is ~270px of margin at either end, which
 * covers a single wheel notch on every platform, and costs 16 extra rows —
 * 288 more `<td>` across both panes, against the ~8,400 removed.
 */
export const OVERSCAN_ROWS = 8;

export interface RowWindow {
  /** First row index to render, inclusive. */
  start: number;
  /** Last row index to render, exclusive. */
  end: number;
  /** Height in px of the spacer standing in for rows `[0, start)`. */
  padTop: number;
  /** Height in px of the spacer standing in for rows `[end, rowCount)`. */
  padBottom: number;
}

export interface RowWindowInput {
  rowCount: number;
  scrollTop: number;
  viewportHeight: number;
  rowHeight: number;
  overscan: number;
}

/**
 * Which slice of rows to mount, and how much empty space to put either side.
 *
 * Every input is defended, because all four of them can legitimately arrive
 * wrong: `scrollTop` can be stale by a frame after the row set shrinks under a
 * filter, `viewportHeight` is zero while the pane is not laid out, `rowHeight`
 * is zero before the first measurement, and `rowCount` is zero until the fetch
 * resolves. Each of those, unguarded, produces either a negative slice (React
 * renders nothing and the pane looks broken) or a NaN height (the spacer
 * collapses and the scrollbar jumps).
 */
export function computeRowWindow({
  rowCount,
  scrollTop,
  viewportHeight,
  rowHeight,
  overscan,
}: RowWindowInput): RowWindow {
  const count = Math.max(0, Math.floor(rowCount) || 0);
  if (count === 0) return { start: 0, end: 0, padTop: 0, padBottom: 0 };

  const height = rowHeight > 0 ? rowHeight : ESTIMATED_ROW_HEIGHT;
  const viewport = viewportHeight > 0 ? viewportHeight : FALLBACK_VIEWPORT_HEIGHT;
  const margin = Math.max(0, Math.floor(overscan) || 0);

  // Clamp the scroll offset into the range the content can actually occupy.
  // A stale offset past the end would otherwise produce start > end.
  const maxScroll = Math.max(0, count * height - viewport);
  const offset = Math.min(Math.max(0, scrollTop || 0), maxScroll);

  const firstVisible = Math.floor(offset / height);
  const visibleCount = Math.ceil(viewport / height) + 1; // +1 for a partial row

  const start = Math.max(0, firstVisible - margin);
  const end = Math.min(count, firstVisible + visibleCount + margin);

  return {
    start,
    end,
    padTop: start * height,
    padBottom: Math.max(0, (count - end) * height),
  };
}

/**
 * The scroll offset that puts row `index` in the middle of the viewport.
 *
 * Centring rather than aligning to the top is deliberate and is what makes
 * click-to-scroll survive virtualization: the clicked cell's counterpart lands
 * clear of the sticky header (which would cover a top-aligned row) and with
 * context above and below it, which is the point of looking at a diff at all.
 * Clamped to the scrollable range so the first and last rows behave.
 */
export function centeredScrollTop(
  index: number,
  rowCount: number,
  rowHeight: number,
  viewportHeight: number,
): number {
  const count = Math.max(0, Math.floor(rowCount) || 0);
  if (count === 0) return 0;

  const height = rowHeight > 0 ? rowHeight : ESTIMATED_ROW_HEIGHT;
  const viewport = viewportHeight > 0 ? viewportHeight : FALLBACK_VIEWPORT_HEIGHT;
  const clamped = Math.min(Math.max(0, Math.floor(index) || 0), count - 1);

  const target = clamped * height + height / 2 - viewport / 2;
  const maxScroll = Math.max(0, count * height - viewport);
  return Math.min(Math.max(0, target), maxScroll);
}
