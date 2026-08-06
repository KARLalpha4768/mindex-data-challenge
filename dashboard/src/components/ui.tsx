"use client";

/**
 * Shared presentational primitives.
 *
 * Kept in one file because each is a handful of lines and splitting them across
 * eight modules would cost more to navigate than it saves. Anything with real
 * behaviour (the code viewer, the explorer) gets its own file.
 */

import React from "react";

import { SEVERITY_STYLES } from "@/lib/config";

/* ── Section scaffolding ──────────────────────────────────────────────────── */

export function SectionHeader({
  title,
  subtitle,
  right,
}: {
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
}) {
  return (
    <header className="mb-5 flex flex-wrap items-end justify-between gap-4 border-b border-line pb-4">
      <div>
        <h2 className="text-lg font-semibold tracking-tight text-ink">{title}</h2>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-ink-dim">{subtitle}</p>}
      </div>
      {right && <div className="shrink-0">{right}</div>}
    </header>
  );
}

/* ── Badges ───────────────────────────────────────────────────────────────── */

export function SeverityBadge({ severity }: { severity: string }) {
  const style = SEVERITY_STYLES[severity] ?? SEVERITY_STYLES.low;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-2xs font-medium uppercase tracking-wide ${style.border} ${style.bg} ${style.text}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} aria-hidden="true" />
      {severity}
    </span>
  );
}

/** Neutral pill. `tone` carries meaning; there is no purely decorative variant. */
export function Badge({
  children,
  tone = "neutral",
  title,
}: {
  children: React.ReactNode;
  tone?: "neutral" | "accent" | "ok" | "warn" | "bad" | "mono";
  title?: string;
}) {
  const tones: Record<string, string> = {
    neutral: "border-line bg-raised text-ink-dim",
    accent: "border-accent-dim bg-accent/10 text-accent",
    ok: "border-ok/40 bg-ok/10 text-ok",
    warn: "border-warn/40 bg-warn/10 text-warn",
    bad: "border-bad/40 bg-bad/10 text-bad",
    mono: "border-line bg-raised text-ink-dim font-mono",
  };
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-2xs ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

/** Monospace code chip for identifiers, column names and defect codes. */
export function Mono({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <code className={`font-mono text-[0.8125rem] text-ink ${className}`}>{children}</code>
  );
}

/* ── Stats ────────────────────────────────────────────────────────────────── */

export function Stat({
  label,
  value,
  sub,
  tone = "neutral",
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  tone?: "neutral" | "accent" | "bad";
}) {
  const valueTone =
    tone === "accent" ? "text-accent" : tone === "bad" ? "text-bad" : "text-ink";
  return (
    <div className="panel p-4">
      <div className="text-2xs font-medium uppercase tracking-wider text-ink-faint">
        {label}
      </div>
      <div className={`mt-2 font-mono text-2xl font-semibold tabular-nums ${valueTone}`}>
        {value}
      </div>
      {sub && <div className="mt-1.5 text-xs leading-snug text-ink-dim">{sub}</div>}
    </div>
  );
}

/* ── Section rationale note ──────────────────────────────────────────────── */

/**
 * A short "why this view exists" note at the head of a section.
 *
 * WHY IT NO LONGER TAKES AN `icon`. It used to render an emoji at 4x weight
 * beside a coloured all-caps title on a shadowed card with a 4px accent rule —
 * five decorative signals for one paragraph of prose, in an application whose
 * stated palette rule is that colour always means something. Against the rest
 * of this dashboard it read as marketing, and the audience is a senior data
 * engineer who discounts a page that shouts.
 *
 * What replaced it is exactly the typography `Field` and `Stat` already use for
 * a label above a value: a 2xs uppercase caption in `ink-faint`, a hairline
 * rule to mark it as an aside, and the prose at the same size as the body text
 * around it. No information was removed from any of the three call sites; only
 * the decoration was.
 */
export function ExecutiveCallout({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-5 border-l border-line-strong py-1 pl-4">
      <div className="text-2xs font-medium uppercase tracking-wider text-ink-faint">{title}</div>
      <div className="mt-1.5 max-w-4xl text-sm leading-relaxed text-ink-dim">{children}</div>
    </div>
  );
}

/* ── Empty state ──────────────────────────────────────────────────────────── */

/**
 * Rendered wherever data is absent. Never a blank area and never a zero: the
 * distinction between "no rows" and "0" is exactly the kind of thing this
 * project is about not blurring.
 */
export function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="panel flex flex-col items-center justify-center gap-1 px-6 py-10 text-center">
      <p className="text-sm font-medium text-ink-dim">{title}</p>
      {detail && <p className="max-w-md text-xs text-ink-faint">{detail}</p>}
    </div>
  );
}

/* ── Labelled prose block, used throughout the defect detail panel ────────── */

export function Field({
  label,
  children,
  className = "",
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      <dt className="text-2xs font-medium uppercase tracking-wider text-ink-faint">
        {label}
      </dt>
      <dd className="mt-1 text-sm leading-relaxed text-ink-dim">{children}</dd>
    </div>
  );
}

/* ── Null-percentage bar (Data Profile) ───────────────────────────────────── */

/**
 * A 0-100% bar. Colour steps rather than a gradient, because the steps encode a
 * judgement: under 1% is noise, over 20% means the column cannot be trusted for
 * grouping without a caveat.
 */
export function NullBar({ pct }: { pct: number }) {
  const tone =
    pct === 0 ? "bg-line-strong" : pct < 1 ? "bg-ok" : pct < 20 ? "bg-warn" : "bg-bad";
  return (
    <div className="flex items-center gap-2">
      <div
        className="h-1.5 w-20 shrink-0 overflow-hidden rounded-full bg-raised"
        role="img"
        aria-label={`${pct.toFixed(2)} percent null`}
      >
        <div className={`h-full rounded-full ${tone}`} style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
      <span className="font-mono text-xs tabular-nums text-ink-dim">{pct.toFixed(2)}%</span>
    </div>
  );
}

/* ── Copy-to-clipboard button ─────────────────────────────────────────────── */

/**
 * Copies `text`. Falls back to a hidden textarea + execCommand where the async
 * Clipboard API is unavailable (non-HTTPS origins, older Safari), because a
 * "copy permalink" button that silently does nothing is worse than not having
 * one.
 */
export function CopyButton({
  text,
  label = "Copy",
  copiedLabel = "Copied",
  className = "",
}: {
  text: string;
  label?: string;
  copiedLabel?: string;
  className?: string;
}) {
  const [copied, setCopied] = React.useState(false);

  const copy = React.useCallback(async () => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }, [text]);

  return (
    <button
      type="button"
      onClick={copy}
      className={`inline-flex items-center gap-1.5 rounded border border-line bg-raised px-2 py-1 text-xs text-ink-dim transition-colors hover:border-line-strong hover:text-ink ${className}`}
    >
      <span aria-live="polite">{copied ? copiedLabel : label}</span>
    </button>
  );
}

/* ── Scrollable table wrapper ─────────────────────────────────────────────── */

/**
 * Horizontal overflow container. `tabIndex={0}` is deliberate: a scrollable
 * region that cannot be reached or scrolled by keyboard is an accessibility
 * failure, and wide analytics tables definitely overflow on a laptop.
 */
export function TableWrap({
  children,
  maxHeight,
  label,
}: {
  children: React.ReactNode;
  maxHeight?: string;
  label?: string;
}) {
  return (
    <div
      tabIndex={0}
      role="region"
      aria-label={label}
      className="panel overflow-auto"
      style={maxHeight ? { maxHeight } : undefined}
    >
      {children}
    </div>
  );
}
