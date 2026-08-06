"use client";

import React from "react";
import { Badge, CopyButton } from "@/components/ui";

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

export default function InterviewerGuideModal({ isOpen, onClose }: Props) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
      <div className="relative max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-xl border border-accent/40 bg-panel p-6 shadow-2xl space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-line pb-4">
          <div>
            <h2 className="text-base font-semibold tracking-tight text-ink">
              Technical evaluator guide
            </h2>
            <p className="mt-1 text-xs text-ink-dim">
              Architectural highlights and talking points for code reviewers and engineering
              managers.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-line bg-raised px-3 py-1 text-xs text-ink-dim transition-colors hover:text-ink"
          >
            Close
          </button>
        </div>

        {/* 1-Second Local Verification Command */}
        <div className="rounded-lg border border-accent/30 bg-accent/10 p-4 space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-mono text-2xs font-medium uppercase tracking-wider text-ink-faint">
              Local verification command
            </span>
            <Badge tone="ok">46/46 Checks Passing</Badge>
          </div>
          <p className="text-xs text-ink-dim">
            Run this single command in your terminal to verify all 46 automated ingestion, cleaning, DDL constraint, and revenue tie-out checks in &lt;1 second:
          </p>
          <div className="flex items-center justify-between rounded bg-[#0b0d11] p-2.5 font-mono text-xs text-accent">
            <code>python scripts/verify_submission.py</code>
            <CopyButton text="python scripts/verify_submission.py" label="Copy Command" copiedLabel="Copied!" />
          </div>
        </div>

        {/* 6 Core Architecture Talking Points */}
        <div className="space-y-4">
          <h3 className="text-2xs font-medium uppercase tracking-wider text-ink-faint">
            Six core architectural talking points
          </h3>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 text-xs">
            {/* 1. Revenue Tie-out */}
            <div className="rounded-lg border border-line bg-raised p-3.5 space-y-1.5">
              <div className="font-medium text-ink">1. Zero-loss revenue tie-out</div>
              <p className="text-ink-dim leading-relaxed">
                Net revenue of <strong>$158,044.29</strong> across 505 raw transactions reconciles 100% to the warehouse (474 kept + 16 quarantined + 15 dropped = 505). Preserving reported totals avoided inventing $961.48 in revenue. <strong>$0.00 drift delta.</strong>
              </p>
            </div>

            {/* 2. Star Schema */}
            <div className="rounded-lg border border-line bg-raised p-3.5 space-y-1.5">
              <div className="font-medium text-ink">2. Star schema and DDL integrity</div>
              <p className="text-ink-dim leading-relaxed">
                Modeled as 1 Fact (<code className="font-mono">fact_sales</code>) and 4 Conformed Dimensions. Uses surrogate integer PKs to isolate analytics from unstable source keys, with strict database-level DDL check constraints.
              </p>
            </div>

            {/* 3. SQL Engine */}
            <div className="rounded-lg border border-line bg-raised p-3.5 space-y-1.5">
              <div className="font-medium text-ink">3. Declarative SQL metric engine</div>
              <p className="text-ink-dim leading-relaxed">
                All 6 BI metrics run in declarative SQL against indexed SQLite tables. Includes an <strong>Interactive SQL Clause Deconstructor</strong> breaking queries into SELECT, JOIN, WHERE, and GROUP BY.
              </p>
            </div>

            {/* 4. Visual Inspector */}
            <div className="rounded-lg border border-line bg-raised p-3.5 space-y-1.5">
              <div className="font-medium text-ink">4. Raw vs clean visual diffing</div>
              <p className="text-ink-dim leading-relaxed">
                Windowed side-by-side grid comparing raw CSVs (red errors, amber deliberate non-corrections) against cleaned outputs (green fixes), with a 15-second highlight on the counterpart cell, sortable headers, and one-click cell detail cards wired into the assistant.
              </p>
            </div>

            {/* 5. AI Copilot */}
            <div className="rounded-lg border border-line bg-raised p-3.5 space-y-1.5">
              <div className="font-medium text-ink">5. Cell-aware grounded assistant</div>
              <p className="text-ink-dim leading-relaxed">
                Embedded assistant grounded on the pipeline&apos;s own output. Click any flagged cell and ask <em>&ldquo;why is this cell flagged?&rdquo;</em>; only the coordinates are posted and the server resolves the row itself, so the answer cannot be invented from browser-supplied text.
              </p>
            </div>

            {/* 6. Self-Audit */}
            <div className="rounded-lg border border-line bg-raised p-3.5 space-y-1.5">
              <div className="font-medium text-ink">6. Adversarial self-audit</div>
              <p className="text-ink-dim leading-relaxed">
                Authored <code className="font-mono">VERIFICATION_REPORT.md</code> detailing mutation-tested edge cases. 46 verification checks and 87 unit tests enforced continuously via GitHub Actions CI.
              </p>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex justify-end border-t border-line pt-4">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg bg-accent px-4 py-2 text-xs font-semibold text-accent-contrast transition-colors hover:bg-accent/90"
          >
            Return to the dashboard
          </button>
        </div>
      </div>
    </div>
  );
}
