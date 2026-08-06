/**
 * Deployment-time constants.
 *
 * Everything a fork would need to change lives in this file. Deliberately not
 * environment variables: a reviewer should be able to see the configuration by
 * reading one short file rather than hunting for a Vercel dashboard setting,
 * and none of it is secret — these values are compiled into the client bundle
 * and are public by construction.
 *
 * The one genuine secret in this project, `GEMINI_API_KEY`, is therefore NOT
 * here. It is read from `process.env` inside `src/lib/chatHandler.ts`, which
 * only the server-side `/api/chat` route imports. The rule that separates the
 * two files: if it can appear in the browser's view-source, it belongs here; if
 * it cannot, it must never be imported by a `"use client"` module.
 */

/**
 * Base URL for deep links out to the code on GitHub.
 *
 * CHANGE THIS ONE CONSTANT when the repo moves. Everything downstream
 * (`githubBlobUrl`) is derived from it. Must point at a `blob/<ref>` path.
 */
export const GITHUB_BASE_URL =
  "https://github.com/KARLalpha4768/mindex-data-challenge/blob/main";

/**
 * Paths inside the bundle's `code_index` are relative to the Python project
 * root (`src/cleaning/...`).
 */
export const REPO_SOURCE_PREFIX = "";

/** Build a permalink to a specific line of pipeline source on GitHub. */
export function githubBlobUrl(path: string, line?: number): string {
  const cleanPath = path.replace(/^\/+/, "");
  const base = REPO_SOURCE_PREFIX
    ? `${GITHUB_BASE_URL}/${REPO_SOURCE_PREFIX}/${cleanPath}`
    : `${GITHUB_BASE_URL}/${cleanPath}`;
  return line ? `${base}#L${line}` : base;
}

/**
 * Business threshold mirrored from `src/config.py:RETURN_RATE_ALERT_THRESHOLD`.
 * Drawn as a reference line on the return-rate chart. If the Python constant
 * changes, change it here too — the bundle does not currently carry it.
 */
export const RETURN_RATE_ALERT_THRESHOLD = 0.1;

/** Severity ordering used for sorting and for the filter control. */
export const SEVERITY_ORDER = ["critical", "high", "medium", "low"] as const;

/** Tailwind class fragments per severity. Colour is semantic, never decorative. */
export const SEVERITY_STYLES: Record<
  string,
  { dot: string; text: string; border: string; bg: string }
> = {
  critical: {
    dot: "bg-critical",
    text: "text-critical",
    border: "border-critical/40",
    bg: "bg-critical/10",
  },
  high: {
    dot: "bg-high",
    text: "text-high",
    border: "border-high/40",
    bg: "bg-high/10",
  },
  medium: {
    dot: "bg-medium",
    text: "text-medium",
    border: "border-medium/40",
    bg: "bg-medium/10",
  },
  low: {
    dot: "bg-low",
    text: "text-low",
    border: "border-low/40",
    bg: "bg-low/10",
  },
};

/**
 * How each audit action should read to a reviewer. The phrasing matters: the
 * point of the dashboard is that "dropped" and "preserved" are both defensible
 * outcomes and the difference is a decision, not an accident.
 */
export const ACTION_LABELS: Record<string, string> = {
  dropped: "Dropped",
  imputed: "Imputed",
  flagged: "Flagged",
  quarantined: "Quarantined",
  preserved: "Preserved",
};

/** Chart palette. One accent plus neutrals; severity colours reserved. */
export const CHART_COLORS = {
  accent: "#5b9dff",
  accentDim: "#2f5fa8",
  grid: "#232830",
  axis: "#6c7480",
  alert: "#f2555a",
  series: ["#5b9dff", "#3fb950", "#d9b23c", "#c77dff", "#f0883e", "#4fd1c5"],
};

/** Section ids — these are the URL hashes, so they are part of the public API. */
export const VIEWS = [
  { id: "overview", label: "Overview" },
  { id: "defects", label: "Defect Explorer" },
  { id: "profile", label: "Data Profile" },
  { id: "lineage", label: "Lineage" },
  { id: "schema", label: "Schema" },
  { id: "analytics", label: "Analytics" },
  { id: "tests", label: "Validation & Tests" },
  { id: "raw", label: "Raw vs Clean CSV" },
  { id: "assistant", label: "Assistant Workspace" },
] as const;

export type ViewId = (typeof VIEWS)[number]["id"];
