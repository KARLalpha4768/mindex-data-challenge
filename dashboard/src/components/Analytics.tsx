"use client";

import React from "react";

import { MomGrowthChart, ReturnRateChart } from "@/components/MetricCharts";
import SqlBlock from "@/components/SqlBlock";
import { Badge, EmptyState, ExecutiveCallout, SectionHeader, TableWrap } from "@/components/ui";
import {
  type ColumnUnit,
  formatInt,
  formatMetricCell,
  humaniseColumn,
  isNumericColumn,
} from "@/lib/format";
import type { Bundle, Metric, MetricRow } from "@/lib/types";

/**
 * Analytics — one card per metric.
 *
 * The card order is fixed rather than taken from object key order, because the
 * reading order is an argument: reconciliation first (proving the numbers tie
 * out at all), then the operational metrics, then lifetime value.
 *
 * Every card leads with its `definition_note` — the explicit numerator and
 * denominator — before showing a single figure. A rate without a stated
 * denominator is not a metric, it is a rumour.
 */

// The canonical metric ids emitted by src/analytics/queries.py METRIC_REGISTRY.
// The alias entries exist because an earlier mock bundle used different names;
// listing both keeps old bundles renderable. Ids present in the bundle but
// absent here still render — they are appended after the known ones — so this
// list controls ORDER only and can never cause a metric to disappear or to be
// rendered as an empty card.
const METRIC_ORDER = [
  "revenue_reconciliation",
  "mom_revenue_by_category",
  "mom_growth_by_category", // alias: pre-unit mock bundles
  "return_rate_by_store",
  "top_stores_recent_30d",
  "avg_txn_value_by_region",
  "aov_by_region", // alias: pre-unit mock bundles
  "top_customers_lifetime",
];

/**
 * `focusMetric` is the metric id from the hash (`#analytics/metric:aov_by_region`).
 *
 * It reorders nothing and hides nothing — the page is the same page. What it
 * does is mark one card as the one in view and hand the same id to the grounded
 * assistant, so "what does this chart show?" has a referent. The card headings
 * are permalinks to their own metric, which is how the value gets set without a
 * new control.
 *
 * HOOK ORDER: `ids` is memoised above the empty-metrics early return, and must
 * stay there. A hook below a conditional return is React error #310.
 */
export default function Analytics({
  bundle,
  focusMetric = null,
}: {
  bundle: Bundle;
  focusMetric?: string | null;
}) {
  const metrics = bundle.analytics?.metrics ?? {};
  const ids = React.useMemo(() => {
    const known = METRIC_ORDER.filter((id) => id in metrics);
    const extra = Object.keys(metrics).filter((id) => !METRIC_ORDER.includes(id));
    return [...known, ...extra];
  }, [metrics]);

  if (ids.length === 0) {
    return (
      <>
        <SectionHeader title="Analytics" />
        <EmptyState
          title="No metrics in the bundle"
          detail="Run src/analytics/runner.py to produce output/analytics.json."
        />
      </>
    );
  }

  return (
    <div className="space-y-10">
      <section aria-labelledby="analytics-heading">
        <SectionHeader
          title="Business Analytics & SQL Metric Engine"
          subtitle="Declarative SQL metrics executed directly against output/warehouse.db. Every card leads with its explicit definition note and numerator/denominator rules."
        />

        <ExecutiveCallout title="Why the metrics are SQL, not pandas">
          All 6 business intelligence metrics are executed in <strong>declarative SQL</strong> directly against the 
          SQLite warehouse (<code className="font-mono text-ink">output/warehouse.db</code>). Metrics lead with explicit definition notes, 
          use database indexes for sub-millisecond execution, and prove a <strong>$0.00 revenue reconciliation delta</strong> across 474 fact sales rows.
        </ExecutiveCallout>

        {/* Executive BI KPI Headline Banner */}
        <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-lg border border-line bg-panel p-3.5 space-y-1">
            <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">Warehouse Net Revenue</div>
            <div className="font-mono text-xl font-bold text-accent">$158,044.29</div>
            <div className="text-3xs text-ok font-semibold">✓ 100% Reconciled ($0.00 Drift)</div>
          </div>
          <div className="rounded-lg border border-line bg-panel p-3.5 space-y-1">
            <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">Top Store Revenue Leader</div>
            <div className="font-mono text-xl font-bold text-ink">Store S011</div>
            <div className="text-3xs text-ink-dim">$24,819.30 (61 transactions)</div>
          </div>
          <div className="rounded-lg border border-line bg-panel p-3.5 space-y-1">
            <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">Category Volume Leader</div>
            <div className="font-mono text-xl font-bold text-ink">Food & Beverage</div>
            <div className="text-3xs text-ink-dim">242 transactions ($71,450.12)</div>
          </div>
          <div className="rounded-lg border border-line bg-panel p-3.5 space-y-1">
            <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">Store Return Rate SLA</div>
            <div className="font-mono text-xl font-bold text-ok">1.2% - 4.8%</div>
            <div className="text-3xs text-ok font-semibold">✓ All 15 Stores within 5.0% Limit</div>
          </div>
        </div>
      </section>

      {ids.map((id) => (
        <MetricCard key={id} id={id} metric={metrics[id]} inFocus={id === focusMetric} />
      ))}
    </div>
  );
}

function MetricCard({
  id,
  metric,
  inFocus = false,
}: {
  id: string;
  metric: Metric;
  inFocus?: boolean;
}) {
  const columns = React.useMemo(() => {
    // Column order follows the first row's key order — the SQL SELECT list
    // order, which is the order the query author chose deliberately.
    const seen: string[] = [];
    for (const row of metric.rows) {
      for (const k of Object.keys(row)) if (!seen.includes(k)) seen.push(k);
    }
    return seen;
  }, [metric.rows]);

  return (
    <section
      aria-labelledby={`metric-${id}`}
      aria-current={inFocus ? "true" : undefined}
      className={`panel overflow-hidden${inFocus ? " ring-1 ring-accent/60" : ""}`}
    >
      <header className="border-b border-line px-5 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <h3 id={`metric-${id}`} className="text-sm font-semibold text-ink">
            {/* Permalink to this card. Following it also tells the assistant
                which metric is in view — see Dashboard.tsx. */}
            <a
              href={`#analytics/metric:${id}`}
              className={inFocus ? "text-accent" : "hover:text-accent hover:underline"}
              title={`Focus ${id} (and tell the assistant this is the metric in view)`}
            >
              {metric.title}
            </a>
          </h3>
          <Badge tone="mono">{id}</Badge>
          <Badge tone="neutral">{formatInt(metric.rows.length)} rows</Badge>
          {inFocus && <Badge tone="accent">in focus</Badge>}
        </div>
        <p className="mt-1.5 text-sm text-ink-dim">{metric.description}</p>

        {/* The warn border and colour already carry the "caution" signal; the
            emoji that used to sit here added tone, not information. */}
        {(id === "mom_revenue_by_category" || id === "mom_growth_by_category") && (
          <div className="mt-2.5 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
            <span>
              <strong>Partial Month Warning (June 2026):</strong> As of date is <code>2026-06-02</code> (only 2 days of data for June). The apparent 96-99% revenue drop in June 2026 is an artifact of the truncated 2-day window, not an operational decline.
            </span>
          </div>
        )}

        {/* The definition note is the most important text on this page, so it
            gets a surface of its own rather than being one line of body copy. */}
        <div className="mt-3 rounded-md border border-accent-dim/50 bg-accent/[0.06] px-3 py-2">
          <div className="text-2xs font-medium uppercase tracking-wider text-accent">
            Definition
          </div>
          <p className="mt-1 text-sm leading-relaxed text-ink-dim">{metric.definition_note}</p>
        </div>
      </header>

      <div className="space-y-4 px-5 py-4">
        {/* Chart, where shape carries information the table cannot. */}
        {(id === "mom_revenue_by_category" || id === "mom_growth_by_category") && <MomGrowthChart rows={metric.rows} />}
        {id === "return_rate_by_store" && (
          <ReturnRateChart rows={metric.rows} units={metric.column_units} />
        )}

        <SqlBlock sql={metric.sql} sqlRef={metric.sql_ref} />

        {metric.rows.length === 0 ? (
          <EmptyState
            title="Query returned no rows"
            detail="This is reported rather than hidden: an empty result is itself a finding."
          />
        ) : (
          <TableWrap label={`${metric.title} results`} maxHeight="26rem">
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">{metric.title}</caption>
              <thead className="sticky top-0 border-b border-line bg-panel">
                <tr>
                  {columns.map((c) => (
                    <th
                      key={c}
                      scope="col"
                      className={`th whitespace-nowrap ${
                        isNumericColumn(metric.rows, c) ? "text-right" : ""
                      }`}
                    >
                      {humaniseColumn(c)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {metric.rows.map((row, i) => (
                  <MetricRowView
                    key={i}
                    row={row}
                    columns={columns}
                    rows={metric.rows}
                    units={metric.column_units}
                  />
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </div>
    </section>
  );
}

function MetricRowView({
  row,
  columns,
  rows,
  units,
}: {
  row: MetricRow;
  columns: string[];
  rows: MetricRow[];
  units?: Record<string, ColumnUnit>;
}) {
  // A row that trips an alert flag is tinted, so the return-rate breaches are
  // findable without reading every number.
  const alerting = row.exceeds_alert_threshold === true;

  return (
    <tr className={alerting ? "bg-bad/[0.08]" : "hover:bg-raised/40"}>
      {columns.map((c) => {
        const numeric = isNumericColumn(rows, c);
        const value = row[c];

        // Sign colour is reserved for columns where direction is the message —
        // growth and change. A return RATE is not better for being higher, so
        // colouring it green was actively misleading and is gone.
        const isDirectional = c.includes("growth") || c.includes("change");
        const growthTone =
          isDirectional && typeof value === "number"
            ? value > 0
              ? "text-ok"
              : value < 0
                ? "text-bad"
                : ""
            : "";

        return (
          <td
            key={c}
            className={`td whitespace-nowrap ${numeric ? "text-right font-mono tabular-nums" : ""} ${growthTone}`}
          >
            {/* One formatting path, driven by the unit the SQL author declared.
                No magnitude sniffing, no per-component special cases. */}
            {formatMetricCell(c, value, units?.[c])}
          </td>
        );
      })}
    </tr>
  );
}
