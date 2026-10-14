"use client";

import React from "react";

import { MomGrowthChart, ReturnRateChart } from "@/components/MetricCharts";
import SqlBlock from "@/components/SqlBlock";
import { Badge, EmptyState, ExecutiveCallout, SectionHeader, TableWrap } from "@/components/ui";
import {
  type ColumnUnit,
  formatCurrency,
  formatInt,
  formatMetricCell,
  humaniseColumn,
  isNumericColumn,
} from "@/lib/format";
import { headlineFigures, type PartialMonth } from "@/lib/publishedMetrics";
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
  const headlines = React.useMemo(() => headlineFigures(metrics), [metrics]);
  const factRows = bundle.run.row_counts.warehouse?.fact_sales;
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
          title="Business Analytics"
          subtitle="Each card is one SQL metric the pipeline ran against the warehouse. The headline figures are read from those rows, not typed beside them."
        />

        <ExecutiveCallout title="Why the metrics are SQL, not pandas">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              {formatInt(ids.length)} metrics were executed in SQL against the warehouse
              {factRows != null ? (
                <>
                  {" "}
                  (<code className="font-mono text-ink">{formatInt(factRows)}</code> fact rows)
                </>
              ) : null}
              . The reconciliation delta on this page is{" "}
              <strong>{formatCurrency(headlines.aggregateDelta)}</strong>, taken from{" "}
              <code className="font-mono text-ink">revenue_reconciliation.aggregate_delta</code>.
            </div>
            <a
              href="#sql"
              className="inline-flex flex-shrink-0 items-center gap-1.5 rounded-lg border border-accent/40 bg-accent/10 px-3 py-1.5 font-mono text-xs font-semibold text-accent transition-colors hover:bg-accent/20"
            >
              Open recorded SQL
            </a>
          </div>
        </ExecutiveCallout>

        <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <HeadlineCard
            label="Net revenue"
            value={formatCurrency(headlines.netRevenue)}
            detail={
              headlines.discountTotal === null
                ? "From revenue_reconciliation"
                : `${formatCurrency(headlines.discountTotal)} of discounts kept (TX-03)`
            }
          />
          <HeadlineCard
            label="Top store, trailing 30 days"
            value={headlines.topStore ? headlines.topStore.storeName : "—"}
            detail={
              headlines.topStore
                ? `${headlines.topStore.storeId} · ${formatCurrency(headlines.topStore.netRevenue)} · ${formatInt(headlines.topStore.transactionCount)} transactions`
                : "top_stores_recent_30d has no rows"
            }
          />
          <HeadlineCard
            label="Reconciliation delta"
            value={formatCurrency(headlines.aggregateDelta)}
            detail="aggregate_delta from the published tie-out"
          />
          <HeadlineCard
            label="Stores over the 10% unit-return threshold"
            value={
              headlines.storesRated === 0
                ? "—"
                : `${formatInt(headlines.breaches.length)} of ${formatInt(headlines.storesRated)}`
            }
            detail={
              headlines.breaches.length
                ? headlines.breaches.map((breach) => breach.storeId).join(", ")
                : headlines.storesRated
                  ? "None of the rated stores exceed the threshold"
                  : "return_rate_by_store has no rows"
            }
            alert={headlines.breaches.length > 0}
          />
        </div>
      </section>

      {ids.map((id) => (
        <MetricCard
          key={id}
          id={id}
          metric={metrics[id]}
          inFocus={id === focusMetric}
          partialMonth={headlines.partialMonth}
          asOfDate={bundle.run.as_of_date}
        />
      ))}
    </div>
  );
}

function HeadlineCard({
  label,
  value,
  detail,
  alert = false,
}: {
  label: string;
  value: string;
  detail: string;
  alert?: boolean;
}) {
  return (
    <div className="space-y-1 rounded-lg border border-line bg-panel p-3.5">
      <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">{label}</div>
      <div className={`font-mono text-xl font-bold ${alert ? "text-bad" : "text-ink"}`}>{value}</div>
      <div className="text-3xs text-ink-dim">{detail}</div>
    </div>
  );
}

function MetricCard({
  id,
  metric,
  inFocus = false,
  partialMonth = null,
  asOfDate,
}: {
  id: string;
  metric: Metric;
  inFocus?: boolean;
  partialMonth?: PartialMonth | null;
  asOfDate: string;
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
        {(id === "mom_revenue_by_category" || id === "mom_growth_by_category") && partialMonth && (
          <div className="mt-2.5 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
            <strong>Partial month ({partialMonth.yearMonth}):</strong> the most any category
            transacted in that month was {formatInt(partialMonth.daysWithData)} distinct{" "}
            {partialMonth.daysWithData === 1 ? "date" : "dates"}, from <code>days_with_data</code>{" "}
            on these rows. A large percentage against that month compares unequal spans. The
            as-of date for this run is <code>{asOfDate}</code>.
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
