"use client";

import React, { useState } from "react";

import { Badge } from "@/components/ui";
import { briefingMarkdown, metricToCsv } from "@/lib/reviewerEvidence";
import type { Bundle, DefectView } from "@/lib/types";

interface Props {
  isOpen: boolean;
  onClose: () => void;
  bundle: Bundle;
  defects: DefectView[];
}

/**
 * Downloads of what this build actually loaded.
 *
 * The previous modal labelled handwritten CSVs as ready to ship. Those
 * files named stores and transaction ids that are not in the warehouse, and
 * a briefing that asserted a pass count the modal had not run. The buttons
 * below serialise the bundle. Warehouse tables live in `solution/output/`
 * after a pipeline run; they are not reconstructed here.
 */
export default function ExportCenterModal({ isOpen, onClose, bundle, defects }: Props) {
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  if (!isOpen) return null;

  const triggerDownload = (filename: string, content: string, mimeType: string) => {
    setDownloadingId(filename);
    try {
      const blob = new Blob([content], { type: `${mimeType};charset=utf-8;` });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } finally {
      setTimeout(() => setDownloadingId(null), 600);
    }
  };

  const metrics = bundle.analytics?.metrics ?? {};
  const metricIds = Object.keys(metrics).filter((id) => metricToCsv(metrics[id]));

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="export-heading"
        className="relative max-h-[90vh] w-full max-w-3xl space-y-6 overflow-y-auto rounded-lg border border-line bg-panel p-6"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line pb-4">
          <div>
            <h2 id="export-heading" className="text-base font-semibold text-ink">
              Export recorded artifacts
            </h2>
            <p className="mt-1 text-xs text-ink-dim">
              JSON and CSV taken from the bundle this dashboard loaded. Warehouse
              tables are written by the pipeline under <code className="font-mono">solution/output/</code>;
              this dialog does not synthesize them.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-line bg-raised px-3 py-1 text-xs text-ink-dim hover:text-ink"
          >
            Close
          </button>
        </div>

        <section className="space-y-3" aria-labelledby="export-metrics">
          <h3 id="export-metrics" className="font-mono text-2xs font-semibold uppercase tracking-wider text-ink-faint">
            Published metrics
          </h3>
          <div className="flex flex-wrap gap-2">
            <DownloadButton
              label="analytics.json"
              busy={downloadingId === "analytics.json"}
              onClick={() =>
                triggerDownload(
                  "analytics.json",
                  JSON.stringify(bundle.analytics ?? {}, null, 2),
                  "application/json",
                )
              }
            />
            {metricIds.map((id) => {
              const filename = `${id}.csv`;
              return (
                <DownloadButton
                  key={id}
                  label={filename}
                  busy={downloadingId === filename}
                  onClick={() => {
                    const csv = metricToCsv(metrics[id]);
                    if (csv) triggerDownload(filename, csv, "text/csv");
                  }}
                />
              );
            })}
          </div>
          {metricIds.length === 0 && (
            <p className="text-xs text-ink-dim">This bundle has no metric rows to export.</p>
          )}
        </section>

        <section className="space-y-3" aria-labelledby="export-catalog">
          <h3 id="export-catalog" className="font-mono text-2xs font-semibold uppercase tracking-wider text-ink-faint">
            Catalog and audit
          </h3>
          <div className="flex flex-wrap gap-2">
            <DownloadButton
              label="defect_catalog.json"
              busy={downloadingId === "defect_catalog.json"}
              onClick={() =>
                triggerDownload(
                  "defect_catalog.json",
                  JSON.stringify(bundle.defect_catalog ?? [], null, 2),
                  "application/json",
                )
              }
            />
            <DownloadButton
              label="audit.json"
              busy={downloadingId === "audit.json"}
              onClick={() =>
                triggerDownload("audit.json", JSON.stringify(bundle.audit ?? [], null, 2), "application/json")
              }
            />
            <DownloadButton
              label="defect_views.json"
              busy={downloadingId === "defect_views.json"}
              onClick={() =>
                triggerDownload(
                  "defect_views.json",
                  JSON.stringify(defects, null, 2),
                  "application/json",
                )
              }
            />
          </div>
          <p className="text-xs text-ink-dim">
            <Badge tone="neutral">{defects.length} defect views</Badge> joined from the catalog,
            the audit log, and the code index. The catalog and audit files are the bundle fields
            unchanged.
          </p>
        </section>

        <section className="space-y-3" aria-labelledby="export-briefing">
          <h3 id="export-briefing" className="font-mono text-2xs font-semibold uppercase tracking-wider text-ink-faint">
            Briefing
          </h3>
          <p className="text-xs text-ink-dim">
            A markdown summary of the run fields and the reconciliation row. Lines are omitted
            when the field is missing. It does not include a test pass count.
          </p>
          <DownloadButton
            label="run_briefing.md"
            busy={downloadingId === "run_briefing.md"}
            onClick={() => triggerDownload("run_briefing.md", briefingMarkdown(bundle), "text/markdown")}
          />
        </section>
      </div>
    </div>
  );
}

function DownloadButton({
  label,
  busy,
  onClick,
}: {
  label: string;
  busy: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded border border-line bg-raised px-2.5 py-1.5 font-mono text-xs text-ink-dim hover:border-line-strong hover:text-ink"
    >
      {busy ? "Saving…" : label}
    </button>
  );
}
