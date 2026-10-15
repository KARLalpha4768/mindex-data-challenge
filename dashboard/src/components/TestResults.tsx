"use client";

import React from "react";

import { Badge, EmptyState, ExecutiveCallout, SectionHeader, TableWrap } from "@/components/ui";
import { formatInt } from "@/lib/format";
import { modulesOf, type TestInventory } from "@/lib/reviewerEvidence";
import type { Bundle } from "@/lib/types";

/**
 * What the suite contains, and the warehouse controls the bundle actually
 * recorded.
 *
 * This page does not run pytest and does not badge a result. The
 * previous version did both: a card of twenty-seven out of twenty-seven, and ten
 * rows pointing at a defects module that is not in this repository. The
 * collected list comes from `pytest --collect-only`, checked back against a
 * fresh collection by `solution/tests/test_dashboard_inventory.py`.
 */
export default function TestResults({
  bundle,
  inventory,
}: {
  bundle: Bundle;
  inventory: TestInventory | null;
}) {
  const factSales = bundle.run.row_counts.warehouse?.fact_sales;
  const fkViolations = bundle.run.row_counts.warehouse?.fk_violations;
  const tieOutCents = bundle.run.row_counts.warehouse?.revenue_tie_out_cents;
  const modules = inventory ? modulesOf(inventory) : [];

  return (
    <div className="space-y-10">
      <section aria-labelledby="tests-heading">
        <SectionHeader
          title="Validation"
          subtitle="The tests pytest collects, plus the warehouse controls this run recorded. Collection is not a result."
        />
        <ExecutiveCallout title="What this page does not show">
          It does not show a pass rate. A collected test has not been run. The
          pass/fail table is{" "}
          <code className="font-mono">python scripts/verify_submission.py</code>{" "}
          in the repository
          {inventory ? (
            <>
              . The list below is the output of{" "}
              <code className="font-mono">
                {inventory.command}
              </code>{" "}
              from <code className="font-mono">{inventory.cwd}/</code>
            </>
          ) : null}
          .
        </ExecutiveCallout>
      </section>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="panel space-y-1 rounded-lg border border-line bg-raised p-4">
          <div className="text-2xs font-medium uppercase tracking-wider text-ink-faint">
            Tests collected
          </div>
          <div className="font-mono text-2xl font-bold text-ink">
            {inventory ? formatInt(inventory.collected) : "—"}
          </div>
          <p className="text-xs text-ink-dim">Collected, not executed, on this page</p>
        </div>
        <div className="panel space-y-1 rounded-lg border border-line bg-raised p-4">
          <div className="text-2xs font-medium uppercase tracking-wider text-ink-faint">
            Revenue tie-out
          </div>
          <div className="font-mono text-2xl font-bold text-ink">
            {tieOutCents == null ? "—" : `${formatInt(tieOutCents)} cents`}
          </div>
          <p className="text-xs text-ink-dim">warehouse.revenue_tie_out_cents from this run</p>
        </div>
        <div className="panel space-y-1 rounded-lg border border-line bg-raised p-4">
          <div className="text-2xs font-medium uppercase tracking-wider text-ink-faint">
            Foreign-key violations
          </div>
          <div className="font-mono text-2xl font-bold text-ink">
            {fkViolations == null ? "—" : formatInt(fkViolations)}
          </div>
          <p className="text-xs text-ink-dim">
            {factSales == null
              ? "Warehouse fact count was not in the bundle"
              : `Across ${formatInt(factSales)} fact rows`}
          </p>
        </div>
      </div>

      <section aria-labelledby="suite-heading">
        <SectionHeader
          title="Collected tests"
          subtitle="Node id and the first line of the docstring. There is no expected-versus-actual column, because this page did not run them."
        />
        {!inventory ? (
          <EmptyState
            title="No test inventory in this build"
            detail="Run python dashboard/scripts/export_test_inventory.py and rebuild."
          />
        ) : (
          <div className="space-y-6">
            {modules.map((module) => (
              <div key={module.file}>
                <h3 className="mb-2 font-mono text-xs text-accent">{module.file}</h3>
                <TableWrap label={`Collected tests in ${module.file}`}>
                  <table className="w-full border-collapse text-sm">
                    <thead className="sticky top-0 border-b border-line bg-panel">
                      <tr>
                        <th scope="col" className="th text-left">
                          Test
                        </th>
                        <th scope="col" className="th text-left">
                          Docstring
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {module.tests.map((test) => (
                        <tr key={test.nodeid} className="hover:bg-raised/40">
                          <td className="td font-mono text-xs text-ink">{test.nodeid.split("::").slice(1).join("::")}</td>
                          <td className="td text-xs text-ink-dim">{test.doc ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableWrap>
                <p className="mt-1 text-2xs text-ink-faint">
                  <Badge tone="neutral">{formatInt(module.tests.length)}</Badge>
                </p>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
