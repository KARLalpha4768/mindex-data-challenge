"use client";

import React from "react";

import { Badge, SectionHeader, SeverityBadge } from "@/components/ui";
import { formatInt } from "@/lib/format";
import { LINEAGE_STAGES } from "@/lib/lineage";
import type { Bundle, DefectView } from "@/lib/types";

/**
 * Lineage — raw CSV to analytics, stage by stage.
 *
 * The claim this view makes is a coverage claim: every one of the defect codes
 * is owned by exactly one stage, so nothing is unhandled and nothing is handled
 * twice. `unownedCodes` below verifies that at render time against the actual
 * catalog — if a code exists in the bundle that no stage claims, it is called
 * out rather than quietly omitted.
 *
 * Every stage that owns codes is a link into the Defect Explorer, pre-filtered
 * to exactly those codes.
 */

export default function Lineage({
  bundle,
  defects,
  onSelectCodes,
}: {
  bundle: Bundle;
  defects: DefectView[];
  onSelectCodes: (codes: string[]) => void;
}) {
  const byCode = React.useMemo(
    () => new Map(defects.map((d) => [d.code, d])),
    [defects],
  );

  // Self-check: codes present in the catalog that no lineage stage claims.
  const owned = new Set(LINEAGE_STAGES.flatMap((s) => s.codes));
  const unownedCodes = defects.map((d) => d.code).filter((c) => !owned.has(c));

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Pipeline lineage"
        subtitle="Raw CSV → profile → clean → star schema → analytics. Each stage lists the defect codes it owns; select a stage to open the Defect Explorer filtered to exactly those codes."
        right={
          <span className="font-mono text-xs text-ink-dim">
            {formatInt(bundle.run.row_counts.raw?.transactions ?? 0)} raw →{" "}
            {formatInt(bundle.run.row_counts.clean?.transactions ?? 0)} fact rows
          </span>
        }
      />

      {unownedCodes.length > 0 && (
        <div className="rounded-md border border-bad/40 bg-bad/10 px-4 py-3 text-sm text-bad">
          {unownedCodes.length} defect code(s) in the bundle are not claimed by any pipeline stage:{" "}
          <span className="font-mono">{unownedCodes.join(", ")}</span>. Update{" "}
          <code className="font-mono">src/lib/lineage.ts</code>.
        </div>
      )}

      {/* The flow. A single column on narrow screens, an alternating rail on
          wide ones — deliberately a list rather than an SVG graph: this is a
          linear pipeline, and a boxes-and-arrows diagram would add visual
          complexity without adding information. */}
      <ol className="relative space-y-3 border-l border-line pl-6">
        {LINEAGE_STAGES.map((stage, index) => {
          const stageDefects = stage.codes
            .map((c) => byCode.get(c))
            .filter((d): d is DefectView => Boolean(d));
          const detected = stageDefects.reduce((sum, d) => sum + (d.detected_count ?? 0), 0);
          const anyMismatch = stageDefects.some((d) => d.coverage !== "match");
          const clickable = stage.codes.length > 0;

          return (
            <li key={stage.id} className="relative">
              {/* Node marker on the rail. */}
              <span
                aria-hidden="true"
                className={`absolute -left-[1.8125rem] top-4 flex h-3.5 w-3.5 items-center justify-center rounded-full border ${
                  clickable
                    ? anyMismatch
                      ? "border-bad bg-bad/30"
                      : "border-accent bg-accent/30"
                    : "border-line-strong bg-raised"
                }`}
              >
                <span className="font-mono text-[0.5rem] text-ink-faint">{index + 1}</span>
              </span>

              <div
                className={`panel p-4 transition-colors ${
                  clickable ? "hover:border-line-strong" : ""
                }`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-sm font-semibold text-ink">{stage.label}</h3>
                    <code className="font-mono text-2xs text-ink-faint">{stage.module}</code>
                  </div>
                  {clickable && (
                    <div className="flex items-center gap-2">
                      <Badge tone={anyMismatch ? "bad" : "accent"}>
                        {formatInt(detected)} rows affected
                      </Badge>
                      <button
                        type="button"
                        onClick={() => onSelectCodes(stage.codes)}
                        className="rounded border border-line bg-raised px-2 py-1 text-xs text-ink-dim transition-colors hover:border-line-strong hover:text-ink"
                      >
                        Open {stage.codes.length} defect{stage.codes.length === 1 ? "" : "s"}
                      </button>
                    </div>
                  )}
                </div>

                <p className="mt-2 max-w-4xl text-sm leading-relaxed text-ink-dim">
                  {stage.summary}
                </p>

                <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-2xs">
                  <div className="flex gap-2">
                    <dt className="uppercase tracking-wider text-ink-faint">in</dt>
                    <dd className="font-mono text-ink-dim">{stage.input}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="uppercase tracking-wider text-ink-faint">out</dt>
                    <dd className="font-mono text-ink-dim">{stage.output}</dd>
                  </div>
                </dl>

                {stageDefects.length > 0 && (
                  <ul className="mt-3 flex flex-wrap gap-1.5 border-t border-line pt-3">
                    {stageDefects.map((d) => (
                      <li key={d.code}>
                        <button
                          type="button"
                          onClick={() => onSelectCodes([d.code])}
                          title={`${d.title} — ${formatInt(d.detected_count)} detected`}
                          className={`inline-flex items-center gap-1.5 rounded border px-2 py-1 transition-colors ${
                            d.coverage === "match"
                              ? "border-line bg-raised hover:border-line-strong"
                              : "border-bad/50 bg-bad/10"
                          }`}
                        >
                          <span className="font-mono text-2xs text-ink">{d.code}</span>
                          <span className="font-mono text-2xs tabular-nums text-ink-faint">
                            {formatInt(d.detected_count)}
                          </span>
                          <SeverityBadge severity={d.severity} />
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
