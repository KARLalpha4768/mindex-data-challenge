"use client";

import React from "react";

import { Badge, ExecutiveCallout, SectionHeader } from "@/components/ui";
import { SCHEMA_NOTES, SCHEMA_TABLES, type SchemaTable } from "@/lib/schema";

/**
 * Schema — the star, rendered from the model in src/lib/schema.ts.
 *
 * Two things this view insists on that a plain DDL dump would not:
 *   1. The GRAIN is stated on every table, in words, at the top. Most warehouse
 *      review conversations that go wrong go wrong because nobody said the grain.
 *   2. Columns that exist to expose a defect finding carry the defect code and
 *      link to it. `extended_amount` and `discount_amount` are not incidental
 *      fields — they are the schema's answer to TX-03.
 */

export default function SchemaView({
  onSelectDefect,
}: {
  onSelectDefect: (code: string) => void;
}) {
  const fact = SCHEMA_TABLES.find((t) => t.kind === "fact");
  const dims = SCHEMA_TABLES.filter((t) => t.kind === "dimension");

  return (
    <div className="space-y-8">
      <SectionHeader
        title="Star Schema Data Warehouse Design"
        subtitle="SQLite warehouse schema (output/warehouse.db) with 1 central fact table and 4 conformed dimensions. Keyed by surrogate integer primary keys with explicit DDL check constraints."
      />

      <ExecutiveCallout title="Star Schema & Data Architecture Rationale" icon="🏛️">
        Modeled as 1 Fact table (<code className="font-mono text-ink">fact_sales</code>) surrounded by 4 Conformed Dimensions (<code className="font-mono text-ink">dim_date</code>, <code className="font-mono text-ink">dim_store</code>, <code className="font-mono text-ink">dim_product</code>, <code className="font-mono text-ink">dim_customer</code>). Uses <strong>surrogate integer primary keys</strong> to isolate historical facts from unstable source keys, and locks in the transaction grain once with DDL check constraints.
      </ExecutiveCallout>

      {/* ── Visual ERD Diagram ─────────────────────────────────────────── */}
      <div className="rounded-lg border border-line bg-panel p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-bold text-ink flex items-center gap-2">
            <span>🗺️</span> Visual Star Schema ERD (Entity-Relationship Diagram)
          </h3>
          <Badge tone="accent">1 Fact : 4 Dimensions</Badge>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-5 gap-3 items-center text-center font-mono text-xs">
          <div className="rounded border border-blue-500/40 bg-blue-500/10 p-3">
            <div className="font-bold text-blue-400">dim_store</div>
            <div className="text-2xs text-ink-dim mt-1">store_key (PK)</div>
            <div className="text-3xs text-ink-faint mt-1">15 surviving stores</div>
          </div>
          
          <div className="hidden md:block font-bold text-accent">FK ➔</div>

          <div className="rounded-lg border-2 border-accent bg-accent/15 p-4 shadow-md">
            <div className="font-bold text-accent text-sm">fact_sales</div>
            <div className="text-2xs text-ink-dim mt-1">sale_key (PK)</div>
            <div className="text-2xs text-accent font-semibold mt-1">474 fact sales</div>
          </div>

          <div className="hidden md:block font-bold text-accent">⬅ FK</div>

          <div className="rounded border border-emerald-500/40 bg-emerald-500/10 p-3">
            <div className="font-bold text-emerald-400">dim_product</div>
            <div className="text-2xs text-ink-dim mt-1">product_key (PK)</div>
            <div className="text-3xs text-ink-faint mt-1">30 products</div>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-center font-mono text-xs">
          <div className="rounded border border-purple-500/40 bg-purple-500/10 p-3">
            <div className="font-bold text-purple-400">dim_customer</div>
            <div className="text-2xs text-ink-dim mt-1">customer_key (PK)</div>
            <div className="text-3xs text-ink-faint mt-1">Conformed Customer Grain</div>
          </div>
          <div className="rounded border border-amber-500/40 bg-amber-500/10 p-3">
            <div className="font-bold text-amber-400">dim_date</div>
            <div className="text-2xs text-ink-dim mt-1">date_key (PK)</div>
            <div className="text-3xs text-ink-faint mt-1">Conformed Date Grain</div>
          </div>
        </div>
      </div>

      {/* ── Design notes ───────────────────────────────────────────────── */}
      <ul className="grid grid-cols-1 gap-2 lg:grid-cols-2">
        {SCHEMA_NOTES.map((note) => (
          <li
            key={note}
            className="rounded-md border border-line bg-panel px-3 py-2 text-sm text-ink-dim"
          >
            {note}
          </li>
        ))}
      </ul>

      {/* ── Fact ───────────────────────────────────────────────────────── */}
      {fact && (
        <section aria-labelledby="fact-heading">
          <h3 id="fact-heading" className="sr-only">
            Fact table
          </h3>
          <TableCard table={fact} onSelectDefect={onSelectDefect} />
        </section>
      )}

      {/* ── Dimensions ─────────────────────────────────────────────────── */}
      <section aria-labelledby="dims-heading">
        <h3 id="dims-heading" className="mb-3 text-sm font-semibold text-ink">
          Dimensions
        </h3>
        <div className="grid grid-cols-1 gap-4 2xl:grid-cols-2">
          {dims.map((t) => (
            <TableCard key={t.name} table={t} onSelectDefect={onSelectDefect} />
          ))}
        </div>
      </section>
    </div>
  );
}

function KeyBadge({ kind }: { kind: string }) {
  const labels: Record<string, { text: string; tone: "accent" | "ok" | "neutral"; title: string }> = {
    pk: { text: "PK", tone: "accent", title: "Primary key (integer surrogate)" },
    fk: { text: "FK", tone: "ok", title: "Foreign key, enforced" },
    nk: { text: "NK", tone: "neutral", title: "Natural key from the source system, UNIQUE NOT NULL" },
  };
  const meta = labels[kind];
  if (!meta) return null;
  return (
    <Badge tone={meta.tone} title={meta.title}>
      {meta.text}
    </Badge>
  );
}

function TableCard({
  table,
  onSelectDefect,
}: {
  table: SchemaTable;
  onSelectDefect: (code: string) => void;
}) {
  return (
    <div className="panel overflow-hidden">
      <header
        className={`border-b px-4 py-3 ${
          table.kind === "fact" ? "border-accent-dim bg-accent/[0.07]" : "border-line"
        }`}
      >
        <div className="flex flex-wrap items-center gap-2">
          <h4 className="font-mono text-sm font-semibold text-ink">{table.name}</h4>
          <Badge tone={table.kind === "fact" ? "accent" : "neutral"}>{table.kind}</Badge>
          <Badge tone="neutral">{table.columns.length} columns</Badge>
        </div>
        <p className="mt-2 text-sm text-ink-dim">{table.purpose}</p>
        <p className="mt-2 text-xs text-ink-dim">
          <span className="font-medium uppercase tracking-wider text-ink-faint">Grain: </span>
          {table.grain}
        </p>
      </header>

      <div tabIndex={0} role="region" aria-label={`${table.name} columns`} className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead className="border-b border-line">
            <tr>
              <th scope="col" className="th">Column</th>
              <th scope="col" className="th w-24">Type</th>
              <th scope="col" className="th w-16">Key</th>
              <th scope="col" className="th">Note</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {table.columns.map((col) => (
              <tr key={col.name} className="hover:bg-raised/40">
                <th scope="row" className="td whitespace-nowrap font-mono font-normal text-ink">
                  {col.name}
                </th>
                <td className="td whitespace-nowrap font-mono text-xs text-ink-faint">
                  {col.type}
                </td>
                <td className="td">{col.key ? <KeyBadge kind={col.key} /> : null}</td>
                <td className="td">
                  {col.note ?? <span className="text-ink-faint">—</span>}
                  {col.defect && (
                    <button
                      type="button"
                      onClick={() => onSelectDefect(col.defect!)}
                      className="ml-2 inline-flex align-middle"
                      title={`Open ${col.defect} in the Defect Explorer`}
                    >
                      <Badge tone="accent">{col.defect}</Badge>
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
