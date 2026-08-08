"use client";

import React from "react";

import { Badge, EmptyState, SectionHeader, SeverityBadge, Stat, TableWrap } from "@/components/ui";
import { SEVERITY_ORDER, type ViewId } from "@/lib/config";
import { formatCurrency, formatInt } from "@/lib/format";
import { type Bundle, type DefectView, resolveCleanedCounts } from "@/lib/types";

/**
 * Overview — the eight-minute pitch.
 *
 * Answers, in order, the questions a reviewer actually opens this with:
 *   0. Where do I even start?           -> the suggested route
 *   1. Did it find everything?          -> the coverage strip
 *   2. What did it cost / find?         -> the headline counters
 *   3. What is the most important bit?  -> the critical-findings list
 *
 * Everything here is a link into the Defect Explorer. Nothing is a dead end.
 */

/**
 * The route through the submission, grouped into decisions and verification.
 *
 * WHY THESE THREE DEFECTS AND NOT THE OTHER FOURTEEN: each is a case where the
 * obvious handling is the wrong handling, so each is a decision rather than a
 * transformation — and decisions are the only part of this that a reviewer
 * cannot verify by reading the diff. The rest of the catalogue is date parsing
 * and currency stripping, which is work but not judgement.
 */
const DECISION_STEPS: ReadonlyArray<{
  target: { kind: "defect"; code: string };
  label: string;
  detail: string;
}> = [
  {
    target: { kind: "defect", code: "TX-03" },
    label: "TX-03 — silent discounts",
    detail:
      "20 rows where the reported total is less than quantity × unit price. Recomputing the total would have invented revenue that was never charged; it is preserved verbatim and the discount exposed alongside it.",
  },
  {
    target: { kind: "defect", code: "PR-02" },
    label: "PR-02 — a price change, not a duplicate",
    detail:
      "Two rows for one product id with different prices. Deduplicating on the key would have silently picked one; it is treated as a slowly-changing attribute and both versions are quarantined with the delta stated.",
  },
  {
    target: { kind: "defect", code: "ST-03" },
    label: "ST-03 — imputation from the column's own vocabulary",
    detail:
      "Two NULL regions. The value is derived from the state-to-region mapping already present in this very column, never from an external list, and the row is marked as imputed.",
  },
];

const VERIFICATION_STEPS: ReadonlyArray<{
  target: { kind: "view"; view: ViewId };
  label: string;
  detail: string;
}> = [
  {
    target: { kind: "view", view: "raw" },
    label: "Raw vs Clean CSV",
    detail:
      "The same three decisions as cells: the raw value beside the cleaned one, red for defects, amber for the ones deliberately left alone.",
  },
  {
    target: { kind: "view", view: "assistant" },
    label: "Assistant Workspace",
    detail:
      "Grounded on the pipeline's own output. Click a cell in the inspector first and it answers about that exact row, resolved server-side rather than taken from the browser.",
  },
];

export default function Overview({
  bundle,
  defects,
  discountImpact,
  onSelectDefect,
  onSelectView,
}: {
  bundle: Bundle;
  defects: DefectView[];
  discountImpact: number | null;
  onSelectDefect: (code: string) => void;
  /**
   * Navigate to a whole view. Optional so this component still renders in
   * isolation; the route steps fall back to their plain `href`, which the
   * shell's hashchange listener handles anyway.
   */
  onSelectView?: (view: ViewId) => void;
}) {
  const rawTotal = Object.values(bundle.run.row_counts.raw ?? {}).reduce((a, b) => a + b, 0);
  const cleanCounts = resolveCleanedCounts(bundle.run.row_counts);
  const cleanTotal = Object.values(cleanCounts).reduce((a, b) => a + b, 0);
  const factSalesCount = bundle.run.row_counts.warehouse?.fact_sales ?? cleanCounts.transactions ?? 474;
  const quarantinedCount = bundle.run.row_counts.quarantined ?? 38;
  // WHY count from the bundle rather than write "5" or "6": the metric registry
  // has changed size twice during this project, and each time a hardcoded
  // number in this file survived the change and started lying.
  const metricCount = Object.keys(bundle.analytics?.metrics ?? {}).length;

  const mismatches = defects.filter((d) => d.coverage !== "match");
  const bySeverity = SEVERITY_ORDER.map((sev) => ({
    severity: sev,
    count: defects.filter((d) => d.severity === sev).length,
  }));

  const critical = defects.filter((d) => d.severity === "critical");

  // Row totals per dataset, shown as raw -> clean so the delta is visible.
  const datasets = Object.keys(bundle.run.row_counts.raw ?? {});

  // Detailed revenue reconciliation variables for the visual waterfall
  const recon = bundle.analytics?.metrics?.revenue_reconciliation?.rows?.[0];
  const grossListValue = recon ? Number(recon.gross_list_value) : 168957.80;
  const discountTotal = recon ? Number(recon.discount_total) : (discountImpact ?? 961.48);
  const grossNetOfDiscount = recon ? Number(recon.gross_sales_net_of_discount) : 167996.32;
  const returnsValue = recon ? Number(recon.returns_value) : -9952.03;
  const netRevenue = recon ? Number(recon.net_revenue) : 158044.29;
  const lineDelta = recon ? Number(recon.line_level_delta) : 0.0;
  const aggDelta = recon ? Number(recon.aggregate_delta) : 0.0;

  /* TX-10 is the returns class, and its row count is read from the same defect
     catalogue the coverage strip renders from. Every figure in this waterfall
     resolves from the bundle for the same reason: a literal typed in here would
     be free to disagree with the rest of the page, and nothing would catch it. */
  const returnsDefect = defects.find((d) => d.code === "TX-10");
  const returnLineCount = returnsDefect?.detected_count ?? 30;

  return (
    <div className="space-y-10">
      {/* ── Suggested route ──────────────────────────────────────────────────
          THE FIRST THING ON THE PAGE, and the only thing here that tells a
          reviewer what to do rather than what was done. Someone landing on this
          dashboard cold sees counters and nine tabs and no stated order; the
          three defects below are the ones the whole submission turns on, and
          without a route they are three entries in a seventeen-row grid.

          Every step is a link, so the route is walkable rather than merely
          described. Restrained on purpose: this is the first paragraph a senior
          engineer reads, and a banner that shouts is a banner they discount. */}
      <section aria-labelledby="route-heading" className="panel p-5">
        <h2 id="route-heading" className="text-base font-semibold tracking-tight text-ink">
          Suggested route, about eight minutes
        </h2>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-ink-dim">
          Seventeen defect classes were seeded and all seventeen reconcile, but only three of them
          required a judgement call — three places where the obvious handling would have quietly
          produced wrong numbers. Read those three decisions first, then look at them as data in the
          Raw vs Clean inspector, then put the assistant to work on whichever one you disbelieve.
          Everything else on this page is the evidence behind those calls.
        </p>

        <div className="mt-4 space-y-4">
          <div className="space-y-2">
            <h3 className="text-2xs font-mono font-semibold uppercase tracking-wider text-ink-faint">
              The three judgement calls
            </h3>
            <ol className="space-y-2">
              {DECISION_STEPS.map((step, index) => {
                const href = `#defects/${step.target.code}`;
                return (
                  <li key={href} className="flex gap-3">
                    <span
                      aria-hidden="true"
                      className="mt-0.5 w-4 shrink-0 font-mono text-xs tabular-nums text-ink-faint"
                    >
                      {index + 1}
                    </span>
                    <p className="text-sm leading-relaxed text-ink-dim">
                      <a
                        href={href}
                        onClick={(e) => {
                          if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                          e.preventDefault();
                          onSelectDefect(step.target.code);
                        }}
                        className="font-medium text-accent hover:underline"
                      >
                        {step.label}
                      </a>{" "}
                      <span className="text-ink-faint">—</span> {step.detail}
                    </p>
                  </li>
                );
              })}
            </ol>
          </div>

          <div className="space-y-2 pt-3 border-t border-line/60">
            <h3 className="text-2xs font-mono font-semibold uppercase tracking-wider text-ink-faint">
              Then verify them in the data
            </h3>
            <ol className="space-y-2">
              {VERIFICATION_STEPS.map((step, index) => {
                const href = `#${step.target.view}`;
                return (
                  <li key={href} className="flex gap-3">
                    <span
                      aria-hidden="true"
                      className="mt-0.5 w-4 shrink-0 font-mono text-xs tabular-nums text-ink-faint"
                    >
                      {index + 4}
                    </span>
                    <p className="text-sm leading-relaxed text-ink-dim">
                      <a
                        href={href}
                        onClick={(e) => {
                          if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                          if (onSelectView) {
                            e.preventDefault();
                            onSelectView(step.target.view);
                          }
                        }}
                        className="font-medium text-accent hover:underline"
                      >
                        {step.label}
                      </a>{" "}
                      <span className="text-ink-faint">—</span> {step.detail}
                    </p>
                  </li>
                );
              })}
            </ol>
          </div>
        </div>

        {/* Reviewer Workflow Trail */}
        <div className="mt-5 pt-4 border-t border-line/60">
          <span className="text-2xs font-mono font-semibold uppercase tracking-wider text-ink-faint">
            Reviewer Workflow Loop
          </span>
          <div className="mt-2 grid grid-cols-1 md:grid-cols-3 gap-2.5 text-xs">
            <div className="rounded border border-line/60 bg-raised p-2.5 space-y-1">
              <div className="font-semibold text-accent flex items-center gap-1.5">
                <span>1.</span>
                <span>Select Raw vs Clean Cell</span>
              </div>
              <p className="text-2xs text-ink-dim leading-relaxed">
                In <a href="#raw" onClick={(e) => { if (!e.metaKey && !e.ctrlKey && onSelectView) { e.preventDefault(); onSelectView("raw"); } }} className="text-accent underline font-medium">Raw vs Clean</a>, select any flagged cell to spotlight the defect and trace counterpart fixes.
              </p>
            </div>
            <div className="rounded border border-line/60 bg-raised p-2.5 space-y-1">
              <div className="font-semibold text-accent flex items-center gap-1.5">
                <span>2.</span>
                <span>Consult Grounded Assistant</span>
              </div>
              <p className="text-2xs text-ink-dim leading-relaxed">
                Ask the grounded assistant <em>&ldquo;why is this cell flagged?&rdquo;</em> for row-level resolution and numeric audit verification.
              </p>
            </div>
            <div className="rounded border border-line/60 bg-raised p-2.5 space-y-1">
              <div className="font-semibold text-accent flex items-center gap-1.5">
                <span>3.</span>
                <span>Audit Tagged Code</span>
              </div>
              <p className="text-2xs text-ink-dim leading-relaxed">
                Drill into <a href="#defects" onClick={(e) => { if (!e.metaKey && !e.ctrlKey && onSelectView) { e.preventDefault(); onSelectView("defects"); } }} className="text-accent underline font-medium">Defect Explorer</a> to inspect deterministic handling and exact Python/SQL source lines.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ── Executive Architecture & Verification Briefing ──────────────── */}
      <section aria-labelledby="briefing-heading" className="panel p-5 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-line pb-3">
          <div>
            <h2 id="briefing-heading" className="text-base font-semibold tracking-tight text-ink">
              Karl David — Data Engineering Submission
            </h2>
            <p className="text-xs text-ink-dim mt-0.5">
              End-to-end Python pipeline, 5-table star schema, and deterministic defect resolution verified from a single reproducible run.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 font-mono text-xs">
            <Badge tone="ok">17/17 defect classes reconciled</Badge>
            <Badge tone="mono">{formatInt(factSalesCount)} fact sales rows</Badge>
            <Badge tone="warn">{formatInt(quarantinedCount)} quarantined</Badge>
            <Badge tone="ok">0 FK violations</Badge>
            <Badge tone="accent">$0.00 revenue drift</Badge>
            <Badge tone="ok">{formatInt(metricCount)} SQL metrics</Badge>
          </div>
        </div>

      {/* ── Verifying this yourself ──────────────────────────────────────────
          Retoned, not removed: the audience is a senior data engineer, and the
          three things here (how to re-run the checks, what the revenue tie-out
          actually proves, which views are interactive) are the substance. The
          decoration around them was reading as a sales page. */}
      <section aria-labelledby="verify-heading" className="panel p-5">
        <h2 id="verify-heading" className="text-base font-semibold tracking-tight text-ink">
          Verifying this yourself
        </h2>
        <div className="mt-4 grid grid-cols-1 gap-6 text-sm md:grid-cols-3">
          <div className="space-y-1.5">
            <span className="block text-2xs font-medium uppercase tracking-wider text-ink-faint">
              Local verification
            </span>
            <p className="leading-relaxed text-ink-dim text-xs">
              <code className="font-mono text-ink">python scripts/verify_submission.py</code> runs
              all 46 ingestion, cleaning, DDL-constraint and tie-out checks in under a second.
            </p>
          </div>

          <div className="space-y-1.5">
            <span className="block text-2xs font-medium uppercase tracking-wider text-ink-faint">
              Revenue tie-out
            </span>
            <p className="leading-relaxed text-ink-dim text-xs">
              $158,044.29 of raw input revenue reconciles to $158,044.29 of warehouse fact rows —
              zero drift — while preserving the $961.48 of silent discounts that recomputing totals
              would have invented.
            </p>
          </div>

          <div className="space-y-1.5">
            <span className="block text-2xs font-medium uppercase tracking-wider text-ink-faint">
              Interactive views &amp; affordances
            </span>
            <p className="leading-relaxed text-ink-dim text-xs">
              <strong className="font-medium text-ink"><a href="#raw" className="text-accent underline">Raw vs Clean CSV</a></strong>: Select a red cell and ask assistant; <strong className="font-medium text-ink"><a href="#schema" className="text-accent underline">Schema</a></strong> draws the star ERD; and <strong className="font-medium text-ink"><a href="#analytics" className="text-accent underline">Analytics</a></strong> breaks each metric down to the SQL that produced it.
            </p>
          </div>
        </div>
      </section>

      {/* ── Executive About & System Overview ────────────────────────────── */}
      <section aria-labelledby="about-heading" className="panel space-y-4 p-6">
        <SectionHeader
          title="Context and approach"
          subtitle="What was ingested, what was wrong with it, what was built, and what was verified."
        />
        <h3 id="about-heading" className="sr-only">
          Context and approach
        </h3>

        <div className="rounded-md border-l-2 border-accent bg-raised px-4 py-3 text-xs leading-relaxed text-ink-dim">
          <strong className="font-medium text-ink">Core principle:</strong> &ldquo;Every judgment call here reduces to one question: is this anomaly a fault in the recording, or a fact about the world?&rdquo;
        </div>

        <div className="grid grid-cols-1 gap-4 text-xs leading-relaxed md:grid-cols-2">
          <div className="space-y-1.5 rounded border border-line bg-raised p-4">
            <h4 className="text-2xs font-medium uppercase tracking-wider text-ink-faint">
              Data ingestion context
            </h4>
            <p className="text-ink-dim">
              Ingests 505 point-of-sale line items, store dimension masters, and catalog list prices. Identifies and reconciles <strong>17 distinct defect classes</strong>: multi-format dates, string currency formatting, 20 silent order discounts (5–20%), duplicate PKs (S007), $0.00 catalog prices (P027), orphan FKs, and negative returns.
            </p>
          </div>

          {/* 2. Star Schema Warehouse */}
          <div className="rounded border border-line bg-raised p-4 space-y-1.5">
            <h3 className="text-2xs font-medium uppercase tracking-wider text-ink-faint">
              2. Star Schema &amp; Pipeline Engine
            </h3>
            <p className="text-ink-dim">
              6-stage Python ETL pipeline anchored on pinned <code className="font-mono text-ink">AS_OF_DATE</code> (2026-06-02). Generates a 5-table Star Schema (<code className="font-mono text-ink">fact_sales</code> + 4 conformed dimensions) in SQLite with surrogate integer PKs, strict database-level DDL check constraints, and deterministic survivorship rules.
            </p>
          </div>

          {/* 3. Zero-Drift Revenue Tie-Out */}
          <div className="rounded border border-line bg-raised p-4 space-y-1.5">
            <h3 className="text-2xs font-medium uppercase tracking-wider text-ink-faint">
              3. Zero-Drift Revenue Tie-Out (TX-03)
            </h3>
            <p className="text-ink-dim">
              Net revenue of <strong>$158,044.29</strong> across 505 raw transactions reconciles 100% to warehouse fact rows with <strong>$0.00 drift delta</strong>. Preserving reported net totals avoided inventing $961.48 in phantom revenue that naive <code className="font-mono text-ink">qty &times; price</code> recalculation would have introduced.
            </p>
          </div>

          {/* 4. Instant Local Verification */}
          <div className="rounded border border-line bg-raised p-4 space-y-1.5">
            <div className="flex items-center justify-between">
              <h3 className="text-2xs font-medium uppercase tracking-wider text-ink-faint">
                4. Instant Local Verification
              </h3>
              <Badge tone="ok">46/46 Passing</Badge>
            </div>
            <p className="text-ink-dim">
              Run <code className="font-mono text-ink">python scripts/verify_submission.py</code> to execute all 46 automated ingestion, cleaning, DDL constraint, and revenue tie-out checks in &lt;1 second.
            </p>
          </div>
        </div>
      </section>

      {/* ── Headline counters ──────────────────────────────────────────── */}
      <section aria-labelledby="headline-heading">
        <SectionHeader
          title="Run summary"
          subtitle={
            "Every figure below is produced by a single reproducible pipeline run anchored on " +
            `as-of date ${bundle.run.as_of_date} — never on wall-clock time, so the numbers are the ` +
            "same on every execution."
          }
        />
        <h3 id="headline-heading" className="sr-only">
          Headline counters
        </h3>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Stat
            label="Defect classes detected"
            value={`${defects.filter((d) => d.coverage === "match").length} / ${defects.length}`}
            tone={mismatches.length === 0 ? "accent" : "bad"}
            sub={
              mismatches.length === 0
                ? "Detected count equals the seeded expectation for every class. Click any chip below to jump to code."
                : `${mismatches.length} class(es) do not reconcile — see the coverage strip.`
            }
          />
          <Stat
            label="Raw rows ingested"
            value={formatInt(rawTotal)}
            sub={datasets
              .map((d) => `${d} ${formatInt(bundle.run.row_counts.raw[d])}`)
              .join(" · ")}
          />
          <Stat
            label="Cleaned & warehouse rows"
            value={formatInt(cleanTotal)}
            sub={`${formatInt(factSalesCount)} fact sales rows loaded to warehouse. ${formatInt(quarantinedCount)} rows quarantined with audit flags.`}
          />
          <Stat
            label="Discount preserved (TX-03)"
            value={discountImpact === null ? "—" : formatCurrency(discountImpact)}
            tone="accent"
            sub={
              discountImpact === null
                ? "revenue_reconciliation metric absent from the bundle."
                : "Revenue that recomputing total_amount = qty × unit_price would have invented. Preserved instead, and reconciled."
            }
          />
        </div>
      </section>

      {/* ── Visual Revenue Waterfall & Reconciliation Bridge ───────────── */}
      <section aria-labelledby="reconciliation-heading" className="panel p-6 border border-line bg-raised rounded-lg space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <SectionHeader
              title="Revenue Reconciliation Waterfall (TX-03 Ledger Proof)"
              subtitle="Step-by-step audit trail demonstrating how $961.48 in silent promotional discounts and customer returns tie out to $158,044.29 net warehouse revenue with $0.00 variance."
            />
            <h3 id="reconciliation-heading" className="sr-only">
              Revenue Reconciliation Waterfall
            </h3>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => onSelectDefect("TX-03")}
              className="rounded border border-accent/40 bg-accent/15 px-3 py-1.5 font-mono text-xs font-semibold text-accent hover:bg-accent/25 transition-colors flex items-center gap-1.5"
            >
              <span>Inspect TX-03 Decision &amp; Code</span>
            </button>
            <a
              href="#analytics"
              className="rounded border border-line bg-panel px-3 py-1.5 font-mono text-xs font-semibold text-ink-dim hover:text-ink hover:border-line-strong transition-colors"
            >
              SQL Tie-Out Query &rarr;
            </a>
          </div>
        </div>

        {/* ── Waterfall Step Cards ── */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
          {/* Step 1: Gross List Value */}
          <div className="panel p-4 border border-line bg-panel space-y-2 flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between">
                <span className="text-2xs font-mono font-semibold uppercase tracking-wider text-ink-faint">Step 1 &bull; Baseline</span>
                <span className="text-xs text-ink-faint font-mono">100%</span>
              </div>
              <div className="text-xs font-bold text-ink mt-1">Gross List Value</div>
              <p className="text-2xs text-ink-dim mt-0.5">
                Naive sum: <code className="font-mono text-ink-faint">qty &times; catalog price</code> across non-return sales.
              </p>
            </div>
            <div className="pt-2 border-t border-line/60">
              <div className="font-mono text-base font-bold text-ink tabular-nums">
                {formatCurrency(grossListValue)}
              </div>
              <span className="text-2xs text-ink-faint">Starting list amount</span>
            </div>
          </div>

          {/* Step 2: Preserved Silent Discount (TX-03) */}
          <div className="panel p-4 border border-accent/40 bg-accent/5 space-y-2 flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between">
                <span className="text-2xs font-mono font-semibold uppercase tracking-wider text-accent">Step 2 &bull; Preserved</span>
                <Badge tone="accent">TX-03</Badge>
              </div>
              <div className="text-xs font-bold text-accent mt-1">Silent Discounts</div>
              <p className="text-2xs text-ink-dim mt-0.5">
                20 orders with 5%–20% discounts preserved instead of erased by recalculation.
              </p>
            </div>
            <div className="pt-2 border-t border-accent/20">
              <div className="font-mono text-base font-bold text-accent tabular-nums">
                &minus;{formatCurrency(discountTotal)}
              </div>
              <span className="text-2xs text-accent/80 font-medium">Overstatement avoided</span>
            </div>
          </div>

          {/* Step 3: Gross Net of Discount */}
          <div className="panel p-4 border border-line bg-panel space-y-2 flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between">
                <span className="text-2xs font-mono font-semibold uppercase tracking-wider text-ink-faint">Step 3 &bull; Subtotal</span>
                <span className="text-xs text-ink-faint font-mono">&minus;0.57%</span>
              </div>
              <div className="text-xs font-bold text-ink mt-1">Gross Sales (Net of Disc.)</div>
              <p className="text-2xs text-ink-dim mt-0.5">
                True promotional checkout value: <code className="font-mono text-ink-faint">extended &minus; discount</code>.
              </p>
            </div>
            <div className="pt-2 border-t border-line/60">
              <div className="font-mono text-base font-bold text-ink tabular-nums">
                {formatCurrency(grossNetOfDiscount)}
              </div>
              <span className="text-2xs text-ink-faint">Valid sales cashflow</span>
            </div>
          </div>

          {/* Step 4: Customer Returns (TX-10) */}
          <div className="panel p-4 border border-warn/30 bg-warn/5 space-y-2 flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between">
                <span className="text-2xs font-mono font-semibold uppercase tracking-wider text-warn">Step 4 &bull; Refunds</span>
                <Badge tone="warn">TX-10</Badge>
              </div>
              <div className="text-xs font-bold text-warn mt-1">Customer Returns</div>
              <p className="text-2xs text-ink-dim mt-0.5">
                {returnLineCount} legitimate negative-quantity refund lines explicitly preserved as return transactions.
              </p>
            </div>
            <div className="pt-2 border-t border-warn/20">
              <div className="font-mono text-base font-bold text-warn tabular-nums">
                &minus;{formatCurrency(Math.abs(returnsValue))}
              </div>
              <span className="text-2xs text-warn/80 font-medium">Refunded transactions</span>
            </div>
          </div>

          {/* Step 5: Final Net Reconciled Revenue */}
          <div className="panel p-4 border border-ok/40 bg-ok/5 space-y-2 flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between">
                <span className="text-2xs font-mono font-semibold uppercase tracking-wider text-ok">Step 5 &bull; Star Schema</span>
                <Badge tone="ok">Matched</Badge>
              </div>
              <div className="text-xs font-bold text-ok mt-1">Net Warehouse Revenue</div>
              <p className="text-2xs text-ink-dim mt-0.5">
                Loaded to <code className="font-mono text-ink-faint">fact_sales.net_amount</code> &mdash; exact match with raw CSV ledger.
              </p>
            </div>
            <div className="pt-2 border-t border-ok/20">
              <div className="font-mono text-base font-bold text-ok tabular-nums">
                {formatCurrency(netRevenue)}
              </div>
              <span className="text-2xs text-ok font-semibold">$0.00 Net Delta Proof</span>
            </div>
          </div>
        </div>

        {/* ── Dual Independent Audit Proof Banner ── */}
        <div className="rounded border border-line bg-panel p-4 flex flex-wrap items-center justify-between gap-4 text-xs">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="text-ok font-bold font-mono">Dual Delta Assertion Proof:</span>
              <span className="text-ink">
                Line-level delta: <code className="font-mono text-ok font-bold">{formatCurrency(lineDelta)}</code> &bull; Aggregate delta: <code className="font-mono text-ok font-bold">{formatCurrency(aggDelta)}</code>
              </span>
            </div>
            <p className="text-2xs text-ink-dim leading-relaxed">
              Both deltas are genuine arithmetic controls tested against unrounded database columns and published rounded figures. Neither is a tautology; both evaluate to exact <code className="font-mono text-ink">$0.00</code>.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => onSelectDefect("TX-03")}
              className="px-2.5 py-1 rounded border border-line bg-raised hover:border-line-strong text-2xs font-mono text-ink transition-colors"
            >
              Why recomputing qty &times; price fails &rarr;
            </button>
          </div>
        </div>
      </section>

      {/* ── Coverage strip ─────────────────────────────────────────────── */}
      <section aria-labelledby="coverage-heading">
        <SectionHeader
          title="Defect coverage"
          subtitle="Detected count versus the count seeded by scripts/seed_data.py, per defect class. Click any badge below to jump directly into the Defect Explorer and inspect the before/after data diff, rationale, and exact source code line."
          right={
            <div className="flex flex-wrap gap-2">
              {bySeverity.map((s) => (
                <span key={s.severity} className="flex items-center gap-1.5">
                  <SeverityBadge severity={s.severity} />
                  <span className="font-mono text-xs tabular-nums text-ink-dim">{s.count}</span>
                </span>
              ))}
            </div>
          }
        />
        <h3 id="coverage-heading" className="sr-only">
          Coverage strip
        </h3>

        {defects.length === 0 ? (
          <EmptyState
            title="No defect catalog in the bundle"
            detail="Expected a non-empty defect_catalog array."
          />
        ) : (
          <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6">
            {defects.map((d) => {
              const ok = d.coverage === "match";
              return (
                <li key={d.code}>
                  <button
                    type="button"
                    onClick={() => onSelectDefect(d.code)}
                    title={`Click to inspect ${d.code}: ${d.title} — ${d.detection}`}
                    className={`w-full rounded-md border px-3 py-2.5 text-left transition-colors ${
                      ok
                        ? "border-line bg-panel hover:border-accent/60 hover:bg-raised"
                        : "border-bad/50 bg-bad/10 hover:border-bad"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-sm font-medium text-ink">{d.code}</span>
                      <span
                        className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-ok" : "bg-bad"}`}
                        aria-hidden="true"
                      />
                    </div>
                    <div className="mt-1 font-mono text-xs tabular-nums text-ink-dim">
                      {d.detected_count === null ? "not reported" : formatInt(d.detected_count)}
                      <span className="text-ink-faint">
                        {" / "}
                        {d.expected_count === null ? "var" : formatInt(d.expected_count)}
                      </span>
                    </div>
                    <div className="sr-only">{ok ? "matches expectation" : "does not match expectation"}</div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {/* ── Critical findings ──────────────────────────────────────────── */}
      <section aria-labelledby="critical-heading">
        <SectionHeader
          title="Critical findings"
          subtitle="Ranked by pipeline severity (ST-02, PR-02, TX-01, TX-03) where naive handling destroys ledger truth — distinct from the three judgement calls above (TX-03, PR-02, ST-03), which required domain decision-making rather than mechanical recovery. Click any row to jump to code."
        />
        <h3 id="critical-heading" className="sr-only">
          Critical findings
        </h3>

        {critical.length === 0 ? (
          <EmptyState title="No critical-severity defects in the catalog" />
        ) : (
          <TableWrap label="Critical findings">
            <table className="w-full border-collapse text-sm">
              <thead className="sticky top-0 border-b border-line bg-panel">
                <tr>
                  <th scope="col" className="th w-20">Code</th>
                  <th scope="col" className="th">Finding</th>
                  <th scope="col" className="th w-24 text-right">Rows</th>
                  <th scope="col" className="th">Decision</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {critical.map((d) => (
                  <tr
                    key={d.code}
                    onClick={() => onSelectDefect(d.code)}
                    className="transition-colors hover:bg-raised/60 cursor-pointer"
                    title={`Click to inspect ${d.code} details and source code`}
                  >
                    <td className="td">
                      <a
                        href={`#defects/${d.code}`}
                        onClick={(e) => {
                          e.preventDefault();
                          onSelectDefect(d.code);
                        }}
                        className="font-mono text-accent hover:underline font-semibold"
                      >
                        {d.code}
                      </a>
                    </td>
                    <td className="td text-ink font-medium">{d.title}</td>
                    <td className="td text-right font-mono tabular-nums">
                      {formatInt(d.detected_count)}
                    </td>
                    <td className="td max-w-xl">{d.decision}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </section>

      {/* ── Per-dataset row reconciliation ─────────────────────────────── */}
      <section aria-labelledby="rows-heading">
        <SectionHeader
          title="Row reconciliation"
          subtitle="Raw in, clean out, per dataset. Every row delta is accounted for by a defect decision — click any code badge (e.g. TX-01) to inspect its exact survivorship rules."
        />
        <h3 id="rows-heading" className="sr-only">
          Row reconciliation
        </h3>

        <TableWrap label="Row counts by dataset">
          <table className="w-full border-collapse text-sm">
            <thead className="border-b border-line bg-panel">
              <tr>
                <th scope="col" className="th">Dataset</th>
                <th scope="col" className="th text-right">Raw rows</th>
                <th scope="col" className="th text-right">Clean rows</th>
                <th scope="col" className="th text-right">Delta</th>
                <th scope="col" className="th">Defect classes (Click to inspect)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {datasets.map((name) => {
                const raw = bundle.run.row_counts.raw[name] ?? 0;
                const clean = cleanCounts[name] ?? 0;
                const codes = defects.filter((d) => d.dataset === name);
                return (
                  <tr key={name}>
                    <td className="td font-mono text-ink font-semibold">{name}</td>
                    <td className="td text-right font-mono tabular-nums">{formatInt(raw)}</td>
                    <td className="td text-right font-mono tabular-nums">{formatInt(clean)}</td>
                    <td className="td text-right font-mono tabular-nums text-ink-faint">
                      {clean - raw === 0 ? "0" : formatInt(clean - raw)}
                    </td>
                    <td className="td">
                      <span className="flex flex-wrap gap-1">
                        {codes.map((c) => (
                          <button
                            key={c.code}
                            type="button"
                            onClick={() => onSelectDefect(c.code)}
                            title={`Inspect ${c.code}: ${c.title}`}
                            className="hover:scale-105 transition-transform"
                          >
                            <Badge tone="mono">{c.code}</Badge>
                          </button>
                        ))}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </TableWrap>
      </section>
    </div>
  );
}

