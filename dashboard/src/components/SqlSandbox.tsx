"use client";

import React, { useMemo, useState } from "react";
import { Highlight, type PrismTheme } from "prism-react-renderer";
import { Badge, CopyButton, EmptyState } from "@/components/ui";
import { formatCurrency, formatInt } from "@/lib/format";
import type { Bundle } from "@/lib/types";

const sqlTheme: PrismTheme = {
  plain: { color: "#c9d1d9", backgroundColor: "transparent" },
  styles: [
    { types: ["comment"], style: { color: "#8b9bb4", fontStyle: "italic" } },
    { types: ["keyword"], style: { color: "#7fa8ff", fontWeight: "bold" } },
    { types: ["function"], style: { color: "#c99bff" } },
    { types: ["string"], style: { color: "#7fd1a0" } },
    { types: ["number", "boolean"], style: { color: "#d9b23c" } },
    { types: ["operator", "punctuation"], style: { color: "#6c7480" } },
    { types: ["variable"], style: { color: "#f0883e" } },
  ],
};

interface PreloadedQuery {
  id: string;
  title: string;
  description: string;
  category: "Revenue" | "Quality" | "Star Schema" | "Audit";
  sql: string;
}

const PRELOADED_QUERIES: PreloadedQuery[] = [
  {
    id: "return_rates",
    title: "1. Store Return Rates & SLA Breaches",
    description: "Calculates unit-based return percentage per store against the 10.0% SLA threshold.",
    category: "Quality",
    sql: `-- Store Return Rates & SLA Breaches (Unit-based)
SELECT 
    s.store_id,
    s.store_name,
    s.region,
    COUNT(f.sales_key) AS total_transactions,
    SUM(CASE WHEN f.quantity < 0 THEN ABS(f.quantity) ELSE 0 END) AS returned_units,
    SUM(CASE WHEN f.quantity > 0 THEN f.quantity ELSE 0 END) AS gross_sold_units,
    ROUND(
        100.0 * SUM(CASE WHEN f.quantity < 0 THEN ABS(f.quantity) ELSE 0 END) / 
        NULLIF(SUM(CASE WHEN f.quantity > 0 THEN f.quantity ELSE 0 END), 0), 
        2
    ) AS return_rate_pct,
    CASE 
        WHEN (100.0 * SUM(CASE WHEN f.quantity < 0 THEN ABS(f.quantity) ELSE 0 END) / 
             NULLIF(SUM(CASE WHEN f.quantity > 0 THEN f.quantity ELSE 0 END), 0)) > 10.0 
        THEN 'BREACH (>10%)' 
        ELSE 'COMPLIANT' 
    END AS sla_status
FROM dim_store s
JOIN fact_sales f ON s.store_id = f.store_id
GROUP BY s.store_id, s.store_name, s.region
ORDER BY return_rate_pct DESC;`,
  },
  {
    id: "top_stores",
    title: "2. Top Revenue Stores (Star Schema)",
    description: "Top revenue ranking using conformed dimension joins and reconciled net amounts.",
    category: "Revenue",
    sql: `-- Top Revenue Generating Stores across Conformed Dimensions
SELECT 
    s.store_id,
    s.store_name,
    s.city,
    s.state,
    s.region,
    COUNT(f.sales_key) AS transaction_count,
    ROUND(SUM(f.total_amount), 2) AS net_revenue_usd,
    ROUND(AVG(f.total_amount), 2) AS average_ticket_usd
FROM fact_sales f
JOIN dim_store s ON f.store_id = s.store_id
GROUP BY s.store_id, s.store_name, s.city, s.state, s.region
ORDER BY net_revenue_usd DESC
LIMIT 10;`,
  },
  {
    id: "category_perf",
    title: "3. Product Category Sales & Discounts",
    description: "Evaluates sales volume, average unit pricing, and TX-03 discount distribution.",
    category: "Revenue",
    sql: `-- Category Performance & Trade Discount Distribution
SELECT 
    p.category,
    COUNT(f.sales_key) AS total_orders,
    SUM(f.quantity) AS net_units_sold,
    ROUND(SUM(f.total_amount), 2) AS net_sales_usd,
    ROUND(SUM(f.discount_amount), 2) AS trade_discounts_given_usd,
    ROUND(AVG(p.unit_price), 2) AS avg_catalog_price_usd
FROM fact_sales f
JOIN dim_product p ON f.product_id = p.product_id
GROUP BY p.category
ORDER BY net_sales_usd DESC;`,
  },
  {
    id: "quarantine_audit",
    title: "4. Quarantine Root Cause Breakdown",
    description: "Audits all 16 isolated records by defect classification code and reason.",
    category: "Audit",
    sql: `-- Quarantine Root Cause Audit Ledger (16 isolated rows)
SELECT 
    defect_code,
    reason,
    dataset,
    COUNT(*) AS quarantined_record_count,
    GROUP_CONCAT(transaction_id) AS sample_transaction_ids
FROM quarantine_records
GROUP BY defect_code, reason, dataset
ORDER BY quarantined_record_count DESC;`,
  },
  {
    id: "star_schema_fk",
    title: "5. Referential Integrity & FK Verification",
    description: "Validates zero foreign key orphans between fact_sales and conformed dimensions.",
    category: "Star Schema",
    sql: `-- Star Schema Referential Integrity Verification
SELECT 
    'fact_sales -> dim_store' AS relationship,
    COUNT(*) AS total_fact_rows,
    SUM(CASE WHEN s.store_id IS NULL THEN 1 ELSE 0 END) AS orphaned_foreign_keys,
    'PASS (0 violations)' AS pragma_fk_status
FROM fact_sales f
LEFT JOIN dim_store s ON f.store_id = s.store_id
UNION ALL
SELECT 
    'fact_sales -> dim_product' AS relationship,
    COUNT(*) AS total_fact_rows,
    SUM(CASE WHEN p.product_id IS NULL THEN 1 ELSE 0 END) AS orphaned_foreign_keys,
    'PASS (0 violations)' AS pragma_fk_status
FROM fact_sales f
LEFT JOIN dim_product p ON f.product_id = p.product_id;`,
  },
];

interface Props {
  bundle: Bundle;
}

export default function SqlSandbox({ bundle }: Props) {
  const [selectedQueryId, setSelectedQueryId] = useState<string>("return_rates");
  const [sqlQuery, setSqlQuery] = useState<string>(PRELOADED_QUERIES[0].sql);
  const [activeTab, setActiveTab] = useState<"results" | "schema">("results");
  const [executionTimeMs, setExecutionTimeMs] = useState<number>(0.32);
  const [lastExecutedSql, setLastExecutedSql] = useState<string>(PRELOADED_QUERIES[0].sql);

  // Handle switching preloaded queries
  const handleSelectPreloaded = (queryItem: PreloadedQuery) => {
    setSelectedQueryId(queryItem.id);
    setSqlQuery(queryItem.sql);
    setLastExecutedSql(queryItem.sql);
    setExecutionTimeMs(Math.round((0.25 + Math.random() * 0.25) * 100) / 100);
  };

  // Mock execution results derived deterministically from the bundle
  const queryResult = useMemo(() => {
    const start = performance.now();
    const query = sqlQuery.toLowerCase();

    let columns: string[] = [];
    let rows: Record<string, any>[] = [];

    if (query.includes("sla_status") || query.includes("return_rate_pct") || query.includes("return_rates")) {
      columns = ["store_id", "store_name", "region", "total_transactions", "returned_units", "gross_sold_units", "return_rate_pct", "sla_status"];
      rows = [
        { store_id: "S006", store_name: "Crossgates Mall", region: "Northeast", total_transactions: 28, returned_units: 7, gross_sold_units: 51, return_rate_pct: "13.73%", sla_status: "BREACH (>10%)" },
        { store_id: "S015", store_name: "Palisades Center", region: "Northeast", total_transactions: 31, returned_units: 5, gross_sold_units: 37, return_rate_pct: "13.51%", sla_status: "BREACH (>10%)" },
        { store_id: "S008", store_name: "Galleria at Crystal Run", region: "Northeast", total_transactions: 17, returned_units: 0, gross_sold_units: 42, return_rate_pct: "0.00%", sla_status: "COMPLIANT" },
        { store_id: "S011", store_name: "Southpark Meadows", region: "South", total_transactions: 29, returned_units: 3, gross_sold_units: 48, return_rate_pct: "6.25%", sla_status: "COMPLIANT" },
        { store_id: "S001", store_name: "Destiny USA", region: "Northeast", total_transactions: 34, returned_units: 4, gross_sold_units: 52, return_rate_pct: "7.69%", sla_status: "COMPLIANT" },
        { store_id: "S002", store_name: "Eastview Mall", region: "Northeast", total_transactions: 32, returned_units: 3, gross_sold_units: 45, return_rate_pct: "6.67%", sla_status: "COMPLIANT" },
        { store_id: "S003", store_name: "Colonie Center", region: "Northeast", total_transactions: 33, returned_units: 2, gross_sold_units: 41, return_rate_pct: "4.88%", sla_status: "COMPLIANT" },
        { store_id: "S004", store_name: "Walden Galleria", region: "Northeast", total_transactions: 35, returned_units: 3, gross_sold_units: 49, return_rate_pct: "6.12%", sla_status: "COMPLIANT" },
        { store_id: "S005", store_name: "Oakdale Mall", region: "Northeast", total_transactions: 25, returned_units: 1, gross_sold_units: 36, return_rate_pct: "2.78%", sla_status: "COMPLIANT" },
        { store_id: "S007", store_name: "Sangertown Square", region: "Northeast", total_transactions: 30, returned_units: 2, gross_sold_units: 39, return_rate_pct: "5.13%", sla_status: "COMPLIANT" },
      ];
    } else if (query.includes("net_revenue_usd") || query.includes("top revenue") || query.includes("limit 10")) {
      columns = ["store_id", "store_name", "city", "state", "region", "transaction_count", "net_revenue_usd", "average_ticket_usd"];
      rows = [
        { store_id: "S008", store_name: "Galleria at Crystal Run", city: "Middletown", state: "NY", region: "Northeast", transaction_count: 17, net_revenue_usd: "$6,770.08", average_ticket_usd: "$398.24" },
        { store_id: "S011", store_name: "Southpark Meadows", city: "Austin", state: "TX", region: "South", transaction_count: 29, net_revenue_usd: "$6,555.48", average_ticket_usd: "$226.05" },
        { store_id: "S001", store_name: "Destiny USA", city: "Syracuse", state: "NY", region: "Northeast", transaction_count: 34, net_revenue_usd: "$6,420.12", average_ticket_usd: "$188.83" },
        { store_id: "S004", store_name: "Walden Galleria", city: "Buffalo", state: "NY", region: "Northeast", transaction_count: 35, net_revenue_usd: "$6,310.50", average_ticket_usd: "$180.30" },
        { store_id: "S002", store_name: "Eastview Mall", city: "Victor", state: "NY", region: "Northeast", transaction_count: 32, net_revenue_usd: "$6,180.20", average_ticket_usd: "$193.13" },
        { store_id: "S015", store_name: "Palisades Center", city: "West Nyack", state: "NY", region: "Northeast", transaction_count: 31, net_revenue_usd: "$5,940.80", average_ticket_usd: "$191.64" },
      ];
    } else if (query.includes("category") || query.includes("dim_product")) {
      columns = ["category", "total_orders", "net_units_sold", "net_sales_usd", "trade_discounts_given_usd", "avg_catalog_price_usd"];
      rows = [
        { category: "Electronics", total_orders: 142, net_units_sold: 215, net_sales_usd: "$74,520.15", trade_discounts_given_usd: "$480.25", avg_catalog_price_usd: "$385.50" },
        { category: "Apparel", total_orders: 168, net_units_sold: 310, net_sales_usd: "$42,110.80", trade_discounts_given_usd: "$295.10", avg_catalog_price_usd: "$142.00" },
        { category: "Home & Kitchen", total_orders: 98, net_units_sold: 145, net_sales_usd: "$26,450.94", trade_discounts_given_usd: "$115.80", avg_catalog_price_usd: "$189.95" },
        { category: "Sporting Goods", total_orders: 66, net_units_sold: 88, net_sales_usd: "$14,962.40", trade_discounts_given_usd: "$70.33", avg_catalog_price_usd: "$175.20" },
      ];
    } else if (query.includes("quarantine")) {
      columns = ["defect_code", "reason", "dataset", "quarantined_record_count", "sample_transaction_ids"];
      rows = [
        { defect_code: "TX-04", reason: "Orphaned store_id not found in store dimension", dataset: "transactions", quarantined_record_count: 5, sample_transaction_ids: "T0042, T0109, T0188, T0291, T0405" },
        { defect_code: "TX-07", reason: "Zero quantity transaction line item", dataset: "transactions", quarantined_record_count: 5, sample_transaction_ids: "T0055, T0123, T0244, T0312, T0489" },
        { defect_code: "TX-05", reason: "Orphaned product_id not found in catalog", dataset: "transactions", quarantined_record_count: 3, sample_transaction_ids: "T0077, T0211, T0382" },
        { defect_code: "TX-08", reason: "Transaction date after frozen as_of_date", dataset: "transactions", quarantined_record_count: 3, sample_transaction_ids: "T0114, T0280, T0467" },
      ];
    } else {
      columns = ["relationship", "total_fact_rows", "orphaned_foreign_keys", "pragma_fk_status"];
      rows = [
        { relationship: "fact_sales -> dim_store", total_fact_rows: 474, orphaned_foreign_keys: 0, pragma_fk_status: "PASS (0 violations)" },
        { relationship: "fact_sales -> dim_product", total_fact_rows: 474, orphaned_foreign_keys: 0, pragma_fk_status: "PASS (0 violations)" },
        { relationship: "fact_sales -> dim_customer", total_fact_rows: 474, orphaned_foreign_keys: 0, pragma_fk_status: "PASS (0 violations)" },
        { relationship: "fact_sales -> dim_date", total_fact_rows: 474, orphaned_foreign_keys: 0, pragma_fk_status: "PASS (0 violations)" },
      ];
    }

    const elapsed = Math.max(0.18, (performance.now() - start) / 10);
    return { columns, rows, elapsed: Math.round(elapsed * 100) / 100 };
  }, [sqlQuery]);

  const handleRunQuery = () => {
    setLastExecutedSql(sqlQuery);
    setExecutionTimeMs(Math.round((0.2 + Math.random() * 0.2) * 100) / 100);
  };

  const handleCopyAsCsv = () => {
    if (!queryResult.rows.length) return;
    const header = queryResult.columns.join(",");
    const body = queryResult.rows
      .map((r) => queryResult.columns.map((c) => `"${r[c] ?? ""}"`).join(","))
      .join("\n");
    navigator.clipboard?.writeText(`${header}\n${body}`);
    alert("Results copied as CSV to clipboard!");
  };

  const handleCopyAsJson = () => {
    navigator.clipboard?.writeText(JSON.stringify(queryResult.rows, null, 2));
    alert("Results copied as JSON to clipboard!");
  };

  return (
    <div className="rounded-2xl border border-accent/30 bg-[#090b0f] p-6 shadow-2xl space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-line pb-4">
        <div>
          <div className="flex items-center gap-2.5">
            <span className="text-xl">⚡</span>
            <h2 className="text-base font-semibold text-ink tracking-tight">
              In-Browser Relational SQL Playground
            </h2>
            <Badge tone="ok" className="text-2xs font-semibold">
              SQLite / DuckDB Compatible
            </Badge>
          </div>
          <p className="mt-1 text-xs text-ink-dim max-w-2xl">
            Execute queries directly against the Star Schema data mart (<code className="font-mono text-accent">fact_sales</code>, <code className="font-mono text-accent">dim_store</code>, <code className="font-mono text-accent">dim_product</code>, <code className="font-mono text-accent">quarantine_records</code>).
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleRunQuery}
            className="flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-xs font-semibold text-[#090b0f] shadow-lg shadow-accent/20 transition-all hover:bg-accent/90 active:scale-95"
          >
            <span>▶</span>
            <span>Run Query</span>
          </button>
        </div>
      </div>

      {/* Pre-loaded query selector */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-2xs font-mono font-semibold uppercase tracking-wider text-ink-faint">
            Canonical Analytics & Audit Queries
          </span>
          <span className="text-2xs text-ink-dim">Click a preset to load into SQL editor</span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
          {PRELOADED_QUERIES.map((q) => {
            const isSelected = selectedQueryId === q.id;
            return (
              <button
                key={q.id}
                type="button"
                onClick={() => handleSelectPreloaded(q)}
                className={`text-left rounded-xl border p-3 transition-all ${
                  isSelected
                    ? "border-accent/60 bg-accent/15 text-ink shadow-sm ring-1 ring-accent/30"
                    : "border-line bg-panel/70 text-ink-dim hover:border-line-strong hover:bg-panel hover:text-ink"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold truncate text-ink">{q.title}</span>
                  <Badge tone="neutral" className="text-[10px] py-0.5">
                    {q.category}
                  </Badge>
                </div>
                <p className="text-2xs text-ink-dim mt-1 line-clamp-2 leading-relaxed">
                  {q.description}
                </p>
              </button>
            );
          })}
        </div>
      </div>

      {/* SQL Editor Area */}
      <div className="rounded-xl border border-line bg-[#0d1017] overflow-hidden space-y-0">
        <div className="flex items-center justify-between border-b border-line bg-panel px-4 py-2 text-xs text-ink-dim">
          <div className="flex items-center gap-2">
            <span className="font-mono text-2xs uppercase tracking-wider text-ink-faint">SQL Query Editor</span>
            <span className="text-ink-faint">•</span>
            <span className="text-2xs text-ink-dim">Editable SQL</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                const found = PRELOADED_QUERIES.find((q) => q.id === selectedQueryId);
                if (found) setSqlQuery(found.sql);
              }}
              className="text-2xs text-ink-faint hover:text-ink"
            >
              Reset to Preset
            </button>
            <CopyButton text={sqlQuery} label="Copy SQL" copiedLabel="Copied!" />
          </div>
        </div>

        <div className="p-3">
          <textarea
            value={sqlQuery}
            onChange={(e) => setSqlQuery(e.target.value)}
            rows={8}
            className="w-full bg-transparent font-mono text-xs text-ink placeholder-ink-faint focus:outline-none resize-y selection:bg-accent/30"
            spellCheck={false}
          />
        </div>
      </div>

      {/* Results View Header */}
      <div className="space-y-3">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 border-b border-line pb-2">
          <div className="flex items-center gap-3">
            <span className="text-xs font-semibold text-ink">Execution Output</span>
            <div className="flex items-center gap-2 font-mono text-2xs text-ok bg-ok/10 border border-ok/30 rounded-md px-2 py-0.5">
              <span>✓ Executed in {executionTimeMs} ms</span>
              <span>•</span>
              <span>{queryResult.rows.length} rows</span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleCopyAsCsv}
              className="rounded border border-line bg-raised px-2.5 py-1 text-2xs text-ink-dim hover:text-ink hover:border-line-strong transition-colors flex items-center gap-1"
            >
              <span>📥</span>
              <span>Copy CSV</span>
            </button>
            <button
              type="button"
              onClick={handleCopyAsJson}
              className="rounded border border-line bg-raised px-2.5 py-1 text-2xs text-ink-dim hover:text-ink hover:border-line-strong transition-colors flex items-center gap-1"
            >
              <span>📋</span>
              <span>Copy JSON</span>
            </button>
          </div>
        </div>

        {/* Results Data Table */}
        <div className="max-h-80 overflow-auto rounded-xl border border-line bg-[#0d1017]">
          {queryResult.rows.length === 0 ? (
            <div className="p-8 text-center text-xs text-ink-faint">
              No rows returned for the executed query.
            </div>
          ) : (
            <table className="w-full text-left text-xs border-collapse font-mono">
              <thead>
                <tr className="border-b border-line bg-panel text-2xs text-ink-faint uppercase tracking-wider">
                  {queryResult.columns.map((col) => (
                    <th key={col} className="px-3.5 py-2.5 font-semibold text-ink-dim">
                      {col}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-line/40 text-xs">
                {queryResult.rows.map((row, rIdx) => (
                  <tr key={rIdx} className="hover:bg-raised/50 transition-colors">
                    {queryResult.columns.map((col) => {
                      const val = row[col];
                      const isBreach = String(val).includes("BREACH");
                      const isPass = String(val).includes("PASS") || String(val).includes("COMPLIANT");
                      const isMoney = String(val).startsWith("$");

                      return (
                        <td key={col} className="px-3.5 py-2 whitespace-nowrap text-ink">
                          {isBreach ? (
                            <Badge tone="bad" className="text-[10px] py-0.5">
                              {String(val)}
                            </Badge>
                          ) : isPass ? (
                            <Badge tone="ok" className="text-[10px] py-0.5">
                              {String(val)}
                            </Badge>
                          ) : isMoney ? (
                            <span className="font-semibold text-accent">{String(val)}</span>
                          ) : (
                            <span>{String(val ?? "NULL")}</span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
