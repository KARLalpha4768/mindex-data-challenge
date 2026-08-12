"use client";

import React, { useMemo, useState } from "react";
import { Badge, Stat } from "@/components/ui";
import { formatCurrency, formatInt } from "@/lib/format";

export default function RevenueSimulator() {
  // Simulator State Levers
  const [recomputePricing, setRecomputePricing] = useState(false); // TX-03
  const [dropReturns, setDropReturns] = useState(false); // TX-10
  const [bypassFkQuarantine, setBypassFkQuarantine] = useState(false); // TX-04, TX-05
  const [bypassDataQuarantine, setBypassDataQuarantine] = useState(false); // TX-07, TX-08

  // Base constants
  const BASELINE_REVENUE = 158044.29;
  const TX03_DISCOUNT_IMPACT = 961.48; // 20 discount transactions
  const TX10_RETURNS_IMPACT = 9952.03; // 30 return transactions
  const BASELINE_FACT_ROWS = 474;
  const BASELINE_QUARANTINE_ROWS = 16;
  const BASELINE_DROPPED_ROWS = 15;

  // Derived Simulated Values
  const simulated = useMemo(() => {
    let revenue = BASELINE_REVENUE;
    let factRows = BASELINE_FACT_ROWS;
    let fkViolations = 0;
    let quarantineRows = BASELINE_QUARANTINE_ROWS;
    let dataQualityAlerts: string[] = [];

    // 1. Pricing Policy (TX-03)
    if (recomputePricing) {
      revenue += TX03_DISCOUNT_IMPACT;
      dataQualityAlerts.push(
        "Invented +$961.48 in fictitious revenue by ignoring unstated trade discounts."
      );
    }

    // 2. Returns Policy (TX-10)
    if (dropReturns) {
      revenue += TX10_RETURNS_IMPACT;
      factRows -= 30; // 30 return rows dropped
      dataQualityAlerts.push(
        "Overstated revenue by +$9,952.03 by dropping 30 legitimate product returns."
      );
    }

    // 3. FK Quarantine Policy (TX-04, TX-05)
    if (bypassFkQuarantine) {
      fkViolations += 8; // 5 store orphans + 3 product orphans
      factRows += 8;
      quarantineRows -= 8;
      dataQualityAlerts.push(
        "Injected 8 Foreign Key violations into fact_sales (5 orphaned stores, 3 orphaned products)."
      );
    }

    // 4. Data Quarantine Policy (TX-07, TX-08)
    if (bypassDataQuarantine) {
      factRows += 8; // 5 zero-quantity + 3 future-dated
      quarantineRows -= 8;
      dataQualityAlerts.push(
        "Ingested 5 zero-quantity transactions and 3 post-dated temporal leakage rows."
      );
    }

    const revenueDelta = revenue - BASELINE_REVENUE;
    const isBaseline =
      !recomputePricing && !dropReturns && !bypassFkQuarantine && !bypassDataQuarantine;

    return {
      revenue,
      revenueDelta,
      factRows,
      fkViolations,
      quarantineRows,
      dataQualityAlerts,
      isBaseline,
    };
  }, [recomputePricing, dropReturns, bypassFkQuarantine, bypassDataQuarantine]);

  const handleReset = () => {
    setRecomputePricing(false);
    setDropReturns(false);
    setBypassFkQuarantine(false);
    setBypassDataQuarantine(false);
  };

  return (
    <div className="rounded-2xl border border-accent/30 bg-gradient-to-b from-[#0d1017] to-[#080a0e] p-6 shadow-xl space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-line pb-4">
        <div>
          <div className="flex items-center gap-2.5">
            <span className="text-xl">🎛️</span>
            <h2 className="text-base font-semibold text-ink tracking-tight">
              Interactive &ldquo;What-If&rdquo; Revenue & Governance Simulator
            </h2>
            <Badge tone={simulated.isBaseline ? "ok" : "warn"} className="text-2xs font-semibold">
              {simulated.isBaseline ? "Ground Truth Baseline" : "Simulated Scenario Active"}
            </Badge>
          </div>
          <p className="mt-1 text-xs text-ink-dim max-w-2xl">
            Test how alternative data cleaning and financial ingestion decisions would impact net
            warehouse revenue, star schema referential integrity, and executive reporting.
          </p>
        </div>

        {!simulated.isBaseline && (
          <button
            type="button"
            onClick={handleReset}
            className="flex items-center gap-1.5 rounded-lg border border-accent/40 bg-accent/10 px-3 py-1.5 text-xs font-semibold text-accent transition-colors hover:bg-accent/20"
          >
            <span>↺</span>
            <span>Reset to Ground Truth Baseline</span>
          </button>
        )}
      </div>

      {/* Comparison Scorecard */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
        {/* Net Revenue */}
        <div className="rounded-xl border border-line bg-panel p-4 space-y-1">
          <span className="text-2xs uppercase tracking-wider text-ink-faint font-mono">
            Simulated Net Revenue
          </span>
          <div className="flex items-baseline gap-2">
            <span className="text-xl font-bold text-ink">
              {formatCurrency(simulated.revenue)}
            </span>
            {simulated.revenueDelta !== 0 && (
              <span
                className={`text-xs font-semibold font-mono ${
                  simulated.revenueDelta > 0 ? "text-critical" : "text-ok"
                }`}
              >
                {simulated.revenueDelta > 0 ? "+" : ""}
                {formatCurrency(simulated.revenueDelta)}
              </span>
            )}
          </div>
          <div className="text-2xs text-ink-dim flex items-center justify-between pt-1">
            <span>Ground Truth:</span>
            <span className="font-mono font-medium text-ink">$158,044.29</span>
          </div>
        </div>

        {/* Financial Drift */}
        <div className="rounded-xl border border-line bg-panel p-4 space-y-1">
          <span className="text-2xs uppercase tracking-wider text-ink-faint font-mono">
            Financial Drift Delta
          </span>
          <div className="flex items-baseline gap-2">
            <span
              className={`text-xl font-bold font-mono ${
                simulated.revenueDelta === 0 ? "text-ok" : "text-critical"
              }`}
            >
              {formatCurrency(simulated.revenueDelta)}
            </span>
          </div>
          <div className="text-2xs text-ink-dim flex items-center justify-between pt-1">
            <span>Status:</span>
            <span className="font-semibold text-ink">
              {simulated.revenueDelta === 0 ? "100% Reconciled" : "Artificial Distortion"}
            </span>
          </div>
        </div>

        {/* Fact Sales Rows */}
        <div className="rounded-xl border border-line bg-panel p-4 space-y-1">
          <span className="text-2xs uppercase tracking-wider text-ink-faint font-mono">
            Fact Sales Row Count
          </span>
          <div className="flex items-baseline gap-2">
            <span className="text-xl font-bold text-ink">{simulated.factRows}</span>
            <span className="text-2xs text-ink-faint">/ 505 raw</span>
          </div>
          <div className="text-2xs text-ink-dim flex items-center justify-between pt-1">
            <span>Quarantined / Dropped:</span>
            <span className="font-mono text-ink">
              {simulated.quarantineRows} / {BASELINE_DROPPED_ROWS}
            </span>
          </div>
        </div>

        {/* Foreign Key Violations */}
        <div className="rounded-xl border border-line bg-panel p-4 space-y-1">
          <span className="text-2xs uppercase tracking-wider text-ink-faint font-mono">
            Star Schema FK Violations
          </span>
          <div className="flex items-baseline gap-2">
            <span
              className={`text-xl font-bold font-mono ${
                simulated.fkViolations === 0 ? "text-ok" : "text-critical"
              }`}
            >
              {simulated.fkViolations}
            </span>
          </div>
          <div className="text-2xs text-ink-dim flex items-center justify-between pt-1">
            <span>Referential Integrity:</span>
            <span className="font-semibold text-ink">
              {simulated.fkViolations === 0 ? "PRAGMA FK = 0" : "Sabotaged"}
            </span>
          </div>
        </div>
      </div>

      {/* Interactive Levers */}
      <div className="space-y-3">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-faint font-mono">
          Architectural Decision Levers
        </h3>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5">
          {/* Lever 1: TX-03 Pricing Policy */}
          <div
            className={`rounded-xl border p-4 transition-all ${
              recomputePricing
                ? "border-critical/60 bg-critical/10"
                : "border-line bg-panel hover:border-line-strong"
            }`}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-xs text-ink">
                    1. Trade Discount Policy (TX-03)
                  </span>
                  <Badge tone={recomputePricing ? "bad" : "ok"} className="text-[10px]">
                    {recomputePricing ? "Flawed: Recompute" : "Best Practice: Preserved"}
                  </Badge>
                </div>
                <p className="mt-1 text-xs text-ink-dim leading-relaxed">
                  {recomputePricing
                    ? "Overwriting reported totals with (quantity * unit_price) invents +$961.48 in synthetic revenue across 20 discounted transactions."
                    : "Preserves the customer-charged amount, respecting unstated trade discounts and preventing revenue fabrication."}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setRecomputePricing(!recomputePricing)}
                className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
                  recomputePricing ? "bg-bad" : "bg-raised border-line"
                }`}
              >
                <span
                  className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${
                    recomputePricing ? "translate-x-5" : "translate-x-0"
                  }`}
                />
              </button>
            </div>
          </div>

          {/* Lever 2: TX-10 Returns Policy */}
          <div
            className={`rounded-xl border p-4 transition-all ${
              dropReturns
                ? "border-bad/60 bg-bad/10"
                : "border-line bg-panel hover:border-line-strong"
            }`}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-xs text-ink">
                    2. Product Returns Accounting (TX-10)
                  </span>
                  <Badge tone={dropReturns ? "bad" : "ok"} className="text-[10px]">
                    {dropReturns ? "Flawed: Dropped" : "Best Practice: Netted"}
                  </Badge>
                </div>
                <p className="mt-1 text-xs text-ink-dim leading-relaxed">
                  {dropReturns
                    ? "Treating negative quantities as 'bad data' and dropping them inflates top-line revenue by +$9,952.03 and blinds executive return rate KPIs."
                    : "Retains return records with negative quantities, accurately deducting -$9,952.03 from net warehouse sales."}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setDropReturns(!dropReturns)}
                className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
                  dropReturns ? "bg-bad" : "bg-raised border-line"
                }`}
              >
                <span
                  className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${
                    dropReturns ? "translate-x-5" : "translate-x-0"
                  }`}
                />
              </button>
            </div>
          </div>

          {/* Lever 3: TX-04 / TX-05 Orphan Quarantine */}
          <div
            className={`rounded-xl border p-4 transition-all ${
              bypassFkQuarantine
                ? "border-bad/60 bg-bad/10"
                : "border-line bg-panel hover:border-line-strong"
            }`}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-xs text-ink">
                    3. Orphaned Foreign Keys (TX-04, TX-05)
                  </span>
                  <Badge tone={bypassFkQuarantine ? "bad" : "ok"} className="text-[10px]">
                    {bypassFkQuarantine ? "Flawed: Ingested" : "Best Practice: Quarantined"}
                  </Badge>
                </div>
                <p className="mt-1 text-xs text-ink-dim leading-relaxed">
                  {bypassFkQuarantine
                    ? "Ingesting orphaned transaction rows creates 8 FK violations (5 missing stores, 3 missing products), corrupting dimension joins."
                    : "Quarantines orphaned rows to separate audit CSVs without data loss, maintaining 100% Star Schema referential integrity."}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setBypassFkQuarantine(!bypassFkQuarantine)}
                className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
                  bypassFkQuarantine ? "bg-bad" : "bg-raised border-line"
                }`}
              >
                <span
                  className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${
                    bypassFkQuarantine ? "translate-x-5" : "translate-x-0"
                  }`}
                />
              </button>
            </div>
          </div>

          {/* Lever 4: TX-07 / TX-08 Zero Qty & Future Dates */}
          <div
            className={`rounded-xl border p-4 transition-all ${
              bypassDataQuarantine
                ? "border-bad/60 bg-bad/10"
                : "border-line bg-panel hover:border-line-strong"
            }`}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-xs text-ink">
                    4. Temporal & Zero-Quantity Filtering (TX-07, TX-08)
                  </span>
                  <Badge tone={bypassDataQuarantine ? "bad" : "ok"} className="text-[10px]">
                    {bypassDataQuarantine ? "Flawed: Ingested" : "Best Practice: Quarantined"}
                  </Badge>
                </div>
                <p className="mt-1 text-xs text-ink-dim leading-relaxed">
                  {bypassDataQuarantine
                    ? "Allows 5 zero-quantity rows and 3 post-dated transactions into fact_sales, distorting Average Order Value (AOV) and introducing leakage."
                    : "Quarantines zero-quantity and future-dated transactions safely, keeping the analytical warehouse clean."}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setBypassDataQuarantine(!bypassDataQuarantine)}
                className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
                  bypassDataQuarantine ? "bg-critical" : "bg-raised border-line"
                }`}
              >
                <span
                  className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${
                    bypassDataQuarantine ? "translate-x-5" : "translate-x-0"
                  }`}
                />
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Scenario Rationale Alert */}
      {simulated.dataQualityAlerts.length > 0 ? (
        <div className="rounded-xl border border-critical/40 bg-critical/10 p-4 space-y-2">
          <div className="flex items-center gap-2 text-critical font-semibold text-xs">
            <span>⚠️</span>
            <span>Data Governance & Financial Distortion Warning</span>
          </div>
          <ul className="list-disc list-inside space-y-1 text-xs text-critical/90">
            {simulated.dataQualityAlerts.map((alert, idx) => (
              <li key={idx}>{alert}</li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="rounded-xl border border-ok/30 bg-ok/10 p-4 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span className="text-ok text-lg">🛡️</span>
            <div>
              <div className="font-semibold text-xs text-ok">
                Production-Ready Financial Integrity Confirmed
              </div>
              <p className="text-2xs text-ink-dim mt-0.5">
                All 505 transactions strictly accounted for: 474 fact sales + 16 quarantined + 15
                dropped duplicate rows. Zero revenue drift, zero FK violations.
              </p>
            </div>
          </div>
          <Badge tone="ok">Audit Proof</Badge>
        </div>
      )}
    </div>
  );
}
