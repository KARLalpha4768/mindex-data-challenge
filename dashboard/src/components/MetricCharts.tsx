"use client";

import React from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { EmptyState } from "@/components/ui";
import { CHART_COLORS, RETURN_RATE_ALERT_THRESHOLD } from "@/lib/config";
import type { MetricRow } from "@/lib/types";

/**
 * The two charts worth drawing.
 *
 * Everything else on the Analytics view is a table, on purpose: a data engineer
 * reads exact figures, and a bar chart of six reconciliation lines would be
 * decoration. These two earn their space because both are about SHAPE —
 * a trajectory over months, and a set of values against a threshold.
 *
 * SSR NOTE: recharts measures the DOM to size itself, so it renders nothing
 * useful during the build-time pre-render and would produce a hydration
 * mismatch. `useMounted` defers both charts to the client and reserves the
 * layout height so nothing jumps when they appear.
 */

function useMounted(): boolean {
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);
  return mounted;
}

/** Shared dark tooltip. recharts' default is a white box. */
const tooltipStyle = {
  contentStyle: {
    background: "#0f1115",
    border: "1px solid #232830",
    borderRadius: "6px",
    fontSize: "12px",
    color: "#e8eaed",
  },
  labelStyle: { color: "#a2a9b4", fontSize: "11px" },
  itemStyle: { color: "#e8eaed" },
  cursor: { fill: "rgba(91,157,255,0.06)" },
};

const axisProps = {
  stroke: CHART_COLORS.axis,
  tick: { fill: CHART_COLORS.axis, fontSize: 11 },
  tickLine: false,
};

function ChartFrame({
  title,
  note,
  height,
  children,
}: {
  title: string;
  note: string;
  height: number;
  children: React.ReactNode;
}) {
  return (
    <figure className="panel p-4">
      <figcaption className="mb-1 text-sm font-medium text-ink">{title}</figcaption>
      <p className="mb-3 text-xs leading-relaxed text-ink-dim">{note}</p>
      <div style={{ height }}>{children}</div>
    </figure>
  );
}

/* ── Month-over-month growth ──────────────────────────────────────────────── */

/**
 * One line per category, x = month, y = net revenue.
 *
 * Plots REVENUE rather than the growth percentage. Growth percentages hide
 * their base — a category going 100 -> 200 and one going 10,000 -> 20,000 draw
 * the identical line — and the percentages are already in the table beneath.
 */
export function MomGrowthChart({ rows }: { rows: MetricRow[] }) {
  const mounted = useMounted();

  const { data, categories } = React.useMemo(() => {
    const getMonth = (r: MetricRow) => String(r.year_month ?? r.month ?? "");
    const getRev = (r: MetricRow) => Number(r.monthly_revenue ?? r.net_revenue ?? 0);

    const months = Array.from(new Set(rows.map(getMonth))).filter(Boolean).sort();
    const cats = Array.from(new Set(rows.map((r) => String(r.category)))).filter(Boolean).sort();
    const byMonth = new Map<string, Record<string, string | number>>(
      months.map((m) => [m, { month: m }]),
    );
    for (const r of rows) {
      const m = getMonth(r);
      const bucket = byMonth.get(m);
      if (bucket) bucket[String(r.category)] = getRev(r);
    }
    return { data: months.map((m) => byMonth.get(m)!), categories: cats };
  }, [rows]);

  if (!rows.length) {
    return <EmptyState title="No month-over-month rows" detail="Metric returned zero rows." />;
  }

  return (
    <ChartFrame
      title="Net revenue by category, by month"
      note="Absolute revenue, not growth percentage — the percentages are in the table below and hide their base. The final month (2026-06) contains only 2 days of data (as-of date 2026-06-02); its revenue drop is a partial-month window artefact, not a trend."
      height={280}
    >
      {mounted ? (
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
            <CartesianGrid stroke={CHART_COLORS.grid} strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="month" {...axisProps} />
            <YAxis
              {...axisProps}
              width={64}
              tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`}
            />
            <Tooltip
              {...tooltipStyle}
              formatter={(v: number | string) => `$${Number(v).toLocaleString()}`}
            />
            <Legend wrapperStyle={{ fontSize: 11, color: "#a2a9b4" }} />
            {categories.map((cat, i) => (
              <Line
                key={cat}
                type="monotone"
                dataKey={cat}
                stroke={CHART_COLORS.series[i % CHART_COLORS.series.length]}
                strokeWidth={1.75}
                dot={{ r: 2 }}
                activeDot={{ r: 4 }}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      ) : (
        <ChartPlaceholder />
      )}
    </ChartFrame>
  );
}

/* ── Return rate by store ─────────────────────────────────────────────────── */

/**
 * Unit-based return rate per store with the alert threshold drawn as a
 * reference line. Bars above the threshold are coloured with the alert colour —
 * the same judgement the `exceeds_alert_threshold` column makes, so the chart
 * and the table can never disagree.
 */
export function ReturnRateChart({ rows }: { rows: MetricRow[] }) {
  const mounted = useMounted();

  const data = React.useMemo(
    () =>
      rows
        .map((r) => {
          let unitVal = Number(r.unit_return_rate_pct ?? r.return_rate_units ?? 0);
          let txVal = Number(r.txn_return_rate_pct ?? r.return_rate_transactions ?? 0);
          // If stored as 0-100 percentage (e.g. 13.73), convert to 0-1 ratio (0.1373)
          if (unitVal > 1) unitVal = unitVal / 100;
          if (txVal > 1) txVal = txVal / 100;
          return {
            store: String(r.store_id),
            name: String(r.store_name ?? r.store_id),
            rate: unitVal,
            txRate: txVal,
          };
        })
        .filter((d) => Number.isFinite(d.rate))
        .sort((a, b) => b.rate - a.rate),
    [rows],
  );

  if (!data.length) {
    return <EmptyState title="No return-rate rows" detail="Metric returned zero rows." />;
  }

  const breaches = data.filter((d) => d.rate > RETURN_RATE_ALERT_THRESHOLD).length;

  return (
    <ChartFrame
      title="Return rate by store (unit-based)"
      note={`SUM(returned units) / SUM(sold units) per store, sorted descending. The dashed line is the ${(RETURN_RATE_ALERT_THRESHOLD * 100).toFixed(0)}% alert threshold from src/config.py; ${breaches} store(s) exceed it.`}
      height={300}
    >
      {mounted ? (
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
            <CartesianGrid stroke={CHART_COLORS.grid} strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="store" {...axisProps} interval={0} />
            <YAxis
              {...axisProps}
              width={52}
              tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
            />
            <Tooltip
              {...tooltipStyle}
              formatter={(v: number | string, key) => [
                `${(Number(v) * 100).toFixed(2)}%`,
                key === "rate" ? "Unit-based" : "Transaction-based",
              ]}
              labelFormatter={(label: string) =>
                data.find((d) => d.store === label)?.name ?? label
              }
            />
            <ReferenceLine
              y={RETURN_RATE_ALERT_THRESHOLD}
              stroke={CHART_COLORS.alert}
              strokeDasharray="4 4"
              label={{
                value: `alert ${(RETURN_RATE_ALERT_THRESHOLD * 100).toFixed(0)}%`,
                position: "right",
                fill: CHART_COLORS.alert,
                fontSize: 10,
              }}
            />
            <Bar dataKey="rate" radius={[2, 2, 0, 0]}>
              {data.map((d) => (
                <Cell
                  key={d.store}
                  fill={
                    d.rate > RETURN_RATE_ALERT_THRESHOLD
                      ? CHART_COLORS.alert
                      : CHART_COLORS.accent
                  }
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      ) : (
        <ChartPlaceholder />
      )}
    </ChartFrame>
  );
}

/** Reserves chart height during the pre-render so nothing shifts on hydration. */
function ChartPlaceholder() {
  return (
    <div
      className="h-full w-full rounded border border-dashed border-line"
      aria-hidden="true"
    />
  );
}
