"use client";

import React from "react";

import CodeViewer from "@/components/CodeViewer";
import { Badge, CopyButton, Field, SeverityBadge } from "@/components/ui";
import { ACTION_LABELS, githubBlobUrl } from "@/lib/config";
import { formatInt } from "@/lib/format";
import { STAGE_BY_CODE } from "@/lib/lineage";
import type { DefectView, SourceFile } from "@/lib/types";

/**
 * Detail panel for one defect.
 *
 * Reading order is the argument: what it is, how it was found, what was done,
 * why that and not the obvious alternative, which rows it touched, and finally
 * the code. A reviewer should be able to stop at any point and have a complete
 * answer down to that depth.
 */

export default function DefectDetail({
  defect,
  sourceFiles,
}: {
  defect: DefectView;
  sourceFiles: Record<string, SourceFile>;
}) {
  const audit = defect.audit;
  const stage = STAGE_BY_CODE[defect.code];

  // Permalink to this exact defect. Built in an effect rather than during
  // render because `window` does not exist while pre-rendering.
  const [permalink, setPermalink] = React.useState("");
  React.useEffect(() => {
    const { origin, pathname } = window.location;
    setPermalink(`${origin}${pathname}#defects/${defect.code}`);
  }, [defect.code]);

  const coverageTone =
    defect.coverage === "match" ? "ok" : defect.coverage === "missing" ? "bad" : "bad";
  const coverageLabel =
    defect.coverage === "match"
      ? "Detected = expected"
      : defect.coverage === "missing"
        ? "Not reported by the run"
        : "Count mismatch";

  // `source_ref` is "path:symbol"; only the path half is linkable on GitHub,
  // but the full string is shown as the link text so the reader sees which
  // function to look for.
  const refPath = defect.source_ref.split(":")[0];

  return (
    <article className="panel flex h-full flex-col overflow-hidden">
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <header className="border-b border-line px-5 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-semibold text-accent">{defect.code}</span>
          <SeverityBadge severity={defect.severity} />
          <Badge tone="mono">{defect.dataset}</Badge>
          {audit && <Badge tone="neutral">{ACTION_LABELS[audit.action] ?? audit.action}</Badge>}
          <Badge tone={coverageTone}>{coverageLabel}</Badge>
        </div>

        <h3 className="mt-2 text-base font-semibold leading-snug text-ink">{defect.title}</h3>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <CopyButton
            text={permalink}
            label="Copy permalink"
            copiedLabel="Permalink copied"
          />
          {refPath && (
            <a
              href={githubBlobUrl(refPath)}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex items-center gap-1.5 rounded border border-line bg-raised px-2 py-1 font-mono text-xs text-ink-dim transition-colors hover:border-line-strong hover:text-ink"
            >
              {defect.source_ref}
              <span className="sr-only"> (opens on GitHub in a new tab)</span>
            </a>
          )}
        </div>
      </header>

      {/* ── Body ───────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto px-5 py-4">
        {/* Counts. Expected vs detected side by side is the whole point of the
            audit log, so it leads. */}
        <div className="grid grid-cols-3 gap-3">
          <CountBox label="Expected" value={defect.expected_count} note="from seed_data.py" />
          <CountBox
            label="Detected"
            value={defect.detected_count}
            note="this run"
            emphasis={defect.coverage !== "match"}
          />
          <div className="rounded-md border border-line bg-raised px-3 py-2">
            <div className="text-2xs font-medium uppercase tracking-wider text-ink-faint">
              Action
            </div>
            <div className="mt-1 text-sm text-ink">
              {audit ? (ACTION_LABELS[audit.action] ?? audit.action) : "—"}
            </div>
          </div>
        </div>

        <dl className="mt-5 space-y-4">
          <Field label="Detection">{defect.detection}</Field>
          <Field label="Decision">
            <span className="text-ink">{defect.decision}</span>
          </Field>
          <Field label="Rationale">{defect.rationale}</Field>
          {audit?.notes && <Field label="Run notes">{audit.notes}</Field>}
          {stage && (
            <Field label="Pipeline stage">
              {stage.label} — <code className="font-mono text-xs">{stage.module}</code>
            </Field>
          )}
        </dl>

        {/* ── Affected business keys ───────────────────────────────────── */}
        <section className="mt-6">
          <h4 className="text-2xs font-medium uppercase tracking-wider text-ink-faint">
            Affected business keys
            {audit && (
              <span className="ml-2 font-mono normal-case tracking-normal text-ink-dim">
                {formatInt(audit.affected_keys.length)} shown
                {defect.detected_count !== null &&
                  audit.affected_keys.length < defect.detected_count &&
                  ` of ${formatInt(defect.detected_count)} (serialiser caps at 50)`}
              </span>
            )}
          </h4>
          {!audit || audit.affected_keys.length === 0 ? (
            <p className="mt-2 text-xs text-ink-faint">
              No keys recorded for this defect class.
            </p>
          ) : (
            <div className="mt-2 flex max-h-32 flex-wrap gap-1 overflow-y-auto rounded border border-line bg-raised/40 p-2">
              {audit.affected_keys.map((k) => (
                <code
                  key={k}
                  className="rounded bg-raised px-1.5 py-0.5 font-mono text-[0.7rem] text-ink-dim"
                >
                  {k}
                </code>
              ))}
            </div>
          )}
        </section>

        {/* ── Code ─────────────────────────────────────────────────────── */}
        <section className="mt-6">
          <h4 className="mb-2 text-2xs font-medium uppercase tracking-wider text-ink-faint">
            Handling code
            <span className="ml-2 normal-case tracking-normal text-ink-faint">
              — located by grepping the pipeline for{" "}
              <code className="font-mono">{`# DEFECT: ${defect.code}`}</code>
            </span>
          </h4>
          <CodeViewer code={defect.code} refs={defect.refs} sourceFiles={sourceFiles} />
        </section>
      </div>
    </article>
  );
}

function CountBox({
  label,
  value,
  note,
  emphasis = false,
}: {
  label: string;
  value: number | null;
  note: string;
  emphasis?: boolean;
}) {
  return (
    <div
      className={`rounded-md border px-3 py-2 ${
        emphasis ? "border-bad/50 bg-bad/10" : "border-line bg-raised"
      }`}
    >
      <div className="text-2xs font-medium uppercase tracking-wider text-ink-faint">{label}</div>
      <div
        className={`mt-1 font-mono text-lg font-semibold tabular-nums ${
          emphasis ? "text-bad" : "text-ink"
        }`}
      >
        {value === null ? "—" : formatInt(value)}
      </div>
      <div className="text-2xs text-ink-faint">{note}</div>
    </div>
  );
}
