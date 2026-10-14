/**
 * Figures a reviewer can see, taken only from the recorded analytics block.
 *
 * WHY THIS MODULE EXISTS
 * ----------------------
 * Two surfaces used to answer business questions with numbers that are not in
 * the warehouse. The SQL view keyword-matched the editor and returned a
 * handwritten table: S006 was "Crossgates Mall", S008's return rate was 0%
 * and labelled COMPLIANT, and a green badge reported a random execution time.
 * The Analytics header said the top store was S011 at $24,819.30 and that
 * every store sat inside a 5% return limit. The recorded run says otherwise:
 * the trailing-30-day leader is S008 at $6,770.08, and three stores exceed
 * the 10% unit-return threshold, S008 among them.
 *
 * A dashboard whose job is to prove a pipeline did not invent revenue cannot
 * invent a different answer beside it. This module has no other source. If a
 * metric is missing, the figure is null and the UI renders an em dash. It
 * does not fall back to a remembered constant.
 *
 * WHAT "RECORDED SQL" MEANS
 * -------------------------
 * Nothing here executes SQL. `resolveRecordedSql` returns a metric's rows
 * only when the editor text is that metric's SQL, whitespace and comments
 * aside. A query that merely mentions `return_rate` is `not_executed`. Fuzzy
 * matching is what let the old view attach a breach table to whatever the
 * reviewer had typed.
 */

import type { ColumnUnit, Metric, MetricRow } from "./types";

/**
 * Reading order, not a filter. Ids present in the bundle but absent here are
 * appended, so a newly published metric still appears. Alias ids keep older
 * bundles renderable. Same list the Analytics cards use, so the two views
 * cannot disagree about which query is "first".
 */
export const PUBLISHED_METRIC_ORDER = [
  "revenue_reconciliation",
  "mom_revenue_by_category",
  "mom_growth_by_category",
  "return_rate_by_store",
  "top_stores_recent_30d",
  "avg_txn_value_by_region",
  "aov_by_region",
  "top_customers_lifetime",
] as const;

export interface PublishedQuery {
  id: string;
  title: string;
  description: string;
  definitionNote: string;
  sql: string;
  sqlRef: string | null;
  columns: string[];
  columnUnits: Record<string, ColumnUnit> | undefined;
  rows: MetricRow[];
}

export type RecordedSqlResolution =
  | { status: "recorded"; query: PublishedQuery }
  | { status: "not_executed"; reason: "empty" | "unpublished" };

export interface TopStoreFigure {
  storeId: string;
  storeName: string;
  netRevenue: number;
  transactionCount: number;
}

export interface ReturnBreach {
  storeId: string;
  storeName: string;
  unitReturnRatePct: number;
}

export interface PartialMonth {
  /** `year_month` whose categories transacted on the fewest distinct dates. */
  yearMonth: string;
  /** Highest per-category `days_with_data` in that month. */
  daysWithData: number;
}

export interface HeadlineFigures {
  netRevenue: number | null;
  discountTotal: number | null;
  aggregateDelta: number | null;
  topStore: TopStoreFigure | null;
  storesRated: number;
  breaches: ReturnBreach[];
  partialMonth: PartialMonth | null;
}

type MetricMap = Record<string, Metric | undefined>;

function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

/** Column order is the first row's key order: the SQL author's SELECT list. */
export function columnsOf(rows: MetricRow[]): string[] {
  const seen: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!seen.includes(key)) seen.push(key);
    }
  }
  return seen;
}

function toQuery(id: string, metric: Metric): PublishedQuery | null {
  const sql = metric.sql?.trim();
  if (!sql || !Array.isArray(metric.rows)) return null;
  return {
    id,
    title: metric.title?.trim() || id,
    description: metric.description?.trim() || "",
    definitionNote: metric.definition_note?.trim() || "",
    sql,
    sqlRef: metric.sql_ref ?? null,
    columns: columnsOf(metric.rows),
    columnUnits: metric.column_units,
    rows: metric.rows,
  };
}

/** Published metrics that carry both SQL and rows, in reading order. */
export function listPublishedQueries(metrics: MetricMap): PublishedQuery[] {
  const known = PUBLISHED_METRIC_ORDER.filter((id) => metrics[id]);
  const extra = Object.keys(metrics).filter(
    (id) => !PUBLISHED_METRIC_ORDER.includes(id as (typeof PUBLISHED_METRIC_ORDER)[number]),
  );
  const queries: PublishedQuery[] = [];
  for (const id of [...known, ...extra]) {
    const metric = metrics[id];
    if (!metric) continue;
    const query = toQuery(id, metric);
    if (query) queries.push(query);
  }
  return queries;
}

/**
 * Identity for "is this the SQL we already ran?".
 *
 * Comments and whitespace are not part of that identity: a reviewer adding a
 * note, or the editor wrapping a line, is still looking at the recorded
 * query. Case is folded because SQLite keywords are case-insensitive and a
 * reformatted `select` must not look like a different question. Everything
 * else — a changed predicate, a different LIMIT, a column renamed — fails
 * the comparison and is reported as not executed.
 */
export function normaliseSql(sql: string): string {
  return sql
    .replace(/--[^\n]*/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

export function resolveRecordedSql(sql: string, metrics: MetricMap): RecordedSqlResolution {
  const needle = normaliseSql(sql);
  if (!needle) return { status: "not_executed", reason: "empty" };
  const hit = listPublishedQueries(metrics).find((query) => normaliseSql(query.sql) === needle);
  if (!hit) return { status: "not_executed", reason: "unpublished" };
  return { status: "recorded", query: hit };
}

/** CSV of the recorded rows. Fields that need quoting are quoted; null is blank. */
export function rowsToCsv(columns: string[], rows: MetricRow[]): string {
  const cell = (value: unknown): string => {
    if (value === null || value === undefined) return "";
    const textValue = String(value);
    if (/[",\n\r]/.test(textValue)) return `"${textValue.replace(/"/g, '""')}"`;
    return textValue;
  };
  const lines = [columns.join(",")];
  for (const row of rows) lines.push(columns.map((column) => cell(row[column])).join(","));
  return lines.join("\n");
}

function breachesOf(rows: MetricRow[]): ReturnBreach[] {
  const breaches: ReturnBreach[] = [];
  for (const row of rows) {
    const flagged = row.exceeds_threshold === 1 || row.exceeds_threshold === true;
    const storeId = text(row.store_id);
    const storeName = text(row.store_name);
    const rate = finite(row.unit_return_rate_pct);
    if (!flagged || !storeId || !storeName || rate === null) continue;
    breaches.push({ storeId, storeName, unitReturnRatePct: rate });
  }
  return breaches;
}

/**
 * The shortest month in the MoM table.
 *
 * `days_with_data` is per category, so the month's figure is the max across
 * its rows — a category that did not trade cannot shrink the month. A month
 * at or above 20 transacting days is not called out; March in this extract
 * has 27 and is a mild truncation, June has one date and is the one a reader
 * will misread as a collapse. Returning the minimum keeps the callout on the
 * month that actually distorts the percentage.
 */
export function shortestMonth(rows: MetricRow[]): PartialMonth | null {
  const byMonth = new Map<string, number>();
  for (const row of rows) {
    const yearMonth = text(row.year_month);
    const days = finite(row.days_with_data);
    if (!yearMonth || days === null) continue;
    byMonth.set(yearMonth, Math.max(byMonth.get(yearMonth) ?? 0, days));
  }
  let shortest: PartialMonth | null = null;
  for (const [yearMonth, daysWithData] of byMonth) {
    if (!shortest || daysWithData < shortest.daysWithData) {
      shortest = { yearMonth, daysWithData };
    }
  }
  if (!shortest || shortest.daysWithData >= 20) return null;
  return shortest;
}

export function headlineFigures(metrics: MetricMap): HeadlineFigures {
  const reconciliation = metrics.revenue_reconciliation?.rows?.[0];
  const top = metrics.top_stores_recent_30d?.rows?.[0];
  const rated = metrics.return_rate_by_store?.rows ?? [];
  const growth =
    metrics.mom_growth_by_category?.rows ?? metrics.mom_revenue_by_category?.rows ?? [];

  const storeId = text(top?.store_id);
  const storeName = text(top?.store_name);
  const netRevenue = finite(top?.net_revenue);
  const transactionCount = finite(top?.transaction_count);

  return {
    netRevenue: finite(reconciliation?.net_revenue),
    discountTotal: finite(reconciliation?.discount_total),
    aggregateDelta: finite(reconciliation?.aggregate_delta),
    topStore:
      storeId && storeName && netRevenue !== null && transactionCount !== null
        ? { storeId, storeName, netRevenue, transactionCount }
        : null,
    storesRated: rated.length,
    breaches: breachesOf(rated),
    partialMonth: shortestMonth(growth),
  };
}
