"use client";

import React from "react";

import Analytics from "@/components/Analytics";
import ChatAssistant from "@/components/ChatAssistant";
import DataProfile from "@/components/DataProfile";
import DefectExplorer from "@/components/DefectExplorer";
import InterviewerGuideModal from "@/components/InterviewerGuideModal";
import Lineage from "@/components/Lineage";
import Overview from "@/components/Overview";
import RawVsCleanInspector from "@/components/RawVsCleanInspector";
import SchemaView from "@/components/SchemaView";
import TestResults from "@/components/TestResults";
import { Badge } from "@/components/ui";
import { VIEWS, type ViewId } from "@/lib/config";
import { formatTimestamp } from "@/lib/format";
import type { Bundle, DefectView } from "@/lib/types";

/**
 * Application shell: navigation, hash routing, and the cross-section
 * navigation callback everything else uses to link into the Defect Explorer.
 *
 * ROUTING
 * -------
 * One Next.js route, six views, addressed by URL hash so every view and every
 * individual defect is linkable and back-button friendly:
 *
 *     #overview
 *     #defects
 *     #defects/TX-03            -> Defect Explorer, TX-03 selected
 *     #defects/codes:TX-01,TX-02 -> Defect Explorer, filtered to those codes
 *
 * Hash rather than the Next router because `output: "export"` produces static
 * files: a real route change would need a separate HTML document per defect,
 * and hash changes cost no navigation at all. It also means a copied permalink
 * works from `file://`.
 */

interface Props {
  bundle: Bundle;
  defects: DefectView[];
  discountImpact: number | null;
  sourceFile: string;
  isMock: boolean;
}

/** Parsed representation of the URL hash. */
interface Route {
  view: ViewId;
  /** A single selected defect code, from `#defects/TX-03`. */
  defect: string | null;
  /** A code allow-list, from `#defects/codes:TX-01,TX-02`. */
  codeFilter: string[] | null;
}

const DEFAULT_ROUTE: Route = { view: "overview", defect: null, codeFilter: null };

const VALID_VIEWS = new Set<string>(VIEWS.map((v) => v.id));

function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, "");
  if (!raw) return DEFAULT_ROUTE;

  const [viewPart, ...rest] = raw.split("/");
  if (!VALID_VIEWS.has(viewPart)) return DEFAULT_ROUTE;

  const view = viewPart as ViewId;
  const param = rest.join("/");
  if (!param) return { view, defect: null, codeFilter: null };

  if (param.startsWith("codes:")) {
    const codes = param
      .slice("codes:".length)
      .split(",")
      .map((c) => c.trim().toUpperCase())
      .filter(Boolean);
    return { view, defect: null, codeFilter: codes.length ? codes : null };
  }

  return { view, defect: param.toUpperCase(), codeFilter: null };
}

export function buildHash(route: Partial<Route> & { view: ViewId }): string {
  if (route.defect) return `#${route.view}/${route.defect}`;
  if (route.codeFilter?.length) return `#${route.view}/codes:${route.codeFilter.join(",")}`;
  return `#${route.view}`;
}

export default function Dashboard({
  bundle,
  defects,
  discountImpact,
  sourceFile,
  isMock,
}: Props) {
  // Start on the default route on both server and first client render so the
  // markup matches; the effect below then applies whatever hash the URL carries.
  // Reading location during render would be a hydration mismatch.
  const [route, setRoute] = React.useState<Route>(DEFAULT_ROUTE);
  const [showGuide, setShowGuide] = React.useState(false);

  React.useEffect(() => {
    const sync = () => setRoute(parseHash(window.location.hash));
    sync();
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  /**
   * Programmatic navigation. Assigning to `location.hash` rather than calling
   * `history.pushState` keeps the browser's own back/forward behaviour intact
   * and re-triggers the `hashchange` listener above, so there is exactly one
   * code path that sets route state.
   */
  const navigate = React.useCallback((next: Partial<Route> & { view: ViewId }) => {
    const hash = buildHash(next);
    if (window.location.hash === hash) {
      setRoute(parseHash(hash)); // Same hash fires no event; sync manually.
    } else {
      window.location.hash = hash;
    }
    // Views swap in place, so scroll position must be reset explicitly.
    window.scrollTo({ top: 0, behavior: "auto" });
  }, []);

  const goToDefect = React.useCallback(
    (code: string) => navigate({ view: "defects", defect: code }),
    [navigate],
  );

  const goToCodes = React.useCallback(
    (codes: string[]) => navigate({ view: "defects", codeFilter: codes }),
    [navigate],
  );

  const mismatches = defects.filter((d) => d.coverage !== "match");

  return (
    <div className="min-h-screen">
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <header className="sticky top-0 z-30 border-b border-line bg-base/90 backdrop-blur supports-[backdrop-filter]:bg-base/70">
        <div className="mx-auto max-w-screen px-4 sm:px-6">
          <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 py-3">
            <div className="flex items-baseline gap-3">
              <h1 className="text-sm font-semibold tracking-tight text-ink">
                Data Quality Review
              </h1>
              <span className="hidden text-xs text-ink-faint sm:inline">
                Mindex · Data Engineer Code Challenge
              </span>
            </div>

            <div className="flex flex-wrap items-center gap-2 text-2xs">
              <button
                type="button"
                onClick={() => setShowGuide(true)}
                className="rounded border border-accent/40 bg-accent/15 px-2.5 py-1 font-mono text-2xs font-semibold text-accent hover:bg-accent/25 transition-colors flex items-center gap-1 shadow-sm"
              >
                <span>🎯</span> Interviewer Guide
              </button>
              <Badge tone="mono" title="Frozen analysis date used by every time-relative metric">
                as_of {bundle.run.as_of_date}
              </Badge>
              <Badge tone="mono" title="Pipeline run timestamp">
                run {formatTimestamp(bundle.run.generated_at)}
              </Badge>
              {mismatches.length === 0 ? (
                <Badge tone="ok">{defects.length}/{defects.length} defect classes reconciled</Badge>
              ) : (
                <Badge tone="bad">{mismatches.length} coverage mismatch</Badge>
              )}
            </div>
          </div>

          {/* ── View navigation ──────────────────────────────────────────
              Real anchors, not buttons: they are keyboard-navigable for free,
              open in a new tab correctly, and show their target in the status
              bar. The hashchange listener does the rest. */}
          <nav aria-label="Dashboard sections" className="-mb-px flex gap-1 overflow-x-auto">
            {VIEWS.map((v) => {
              const active = route.view === v.id;
              return (
                <a
                  key={v.id}
                  href={`#${v.id}`}
                  aria-current={active ? "page" : undefined}
                  className={`whitespace-nowrap border-b-2 px-3 py-2 text-sm transition-colors ${
                    active
                      ? "border-accent text-ink"
                      : "border-transparent text-ink-faint hover:border-line-strong hover:text-ink-dim"
                  }`}
                >
                  {v.label}
                </a>
              );
            })}
          </nav>
        </div>
      </header>

      {/* ── Mock-data banner ───────────────────────────────────────────────
          Loud on purpose. A reviewer must never be able to mistake the
          committed stand-in for real pipeline output. */}
      {isMock && (
        <div className="border-b border-warn/30 bg-warn/10">
          <div className="mx-auto max-w-screen px-4 py-2 text-xs text-warn sm:px-6">
            Rendering <code className="font-mono">public/data/{sourceFile}</code> — the committed
            stand-in bundle. Counts and keys are computed from the real{" "}
            <code className="font-mono">data/raw/*.csv</code>; the source excerpts are
            representative. Drop the pipeline&apos;s{" "}
            <code className="font-mono">output/dashboard_bundle.json</code> at{" "}
            <code className="font-mono">public/data/bundle.json</code> and rebuild to replace it.
          </div>
        </div>
      )}

      {/* ── Views ──────────────────────────────────────────────────────── */}
      <main id="main" className="mx-auto max-w-screen px-4 py-6 sm:px-6 sm:py-8">
        {route.view === "overview" && (
          <Overview
            bundle={bundle}
            defects={defects}
            discountImpact={discountImpact}
            onSelectDefect={goToDefect}
          />
        )}

        {route.view === "defects" && (
          <DefectExplorer
            bundle={bundle}
            defects={defects}
            selectedCode={route.defect}
            codeFilter={route.codeFilter}
            onSelectDefect={goToDefect}
          />
        )}

        {route.view === "profile" && <DataProfile bundle={bundle} />}

        {route.view === "lineage" && (
          <Lineage bundle={bundle} defects={defects} onSelectCodes={goToCodes} />
        )}

        {route.view === "schema" && <SchemaView onSelectDefect={goToDefect} />}

        {route.view === "analytics" && <Analytics bundle={bundle} />}

        {route.view === "tests" && <TestResults bundle={bundle} />}

        {route.view === "raw" && <RawVsCleanInspector bundle={bundle} onSelectDefect={goToDefect} />}

        {route.view === "assistant" && (
          <div className="mx-auto max-w-5xl">
            <ChatAssistant bundle={bundle} defects={defects} onSelectDefect={goToDefect} forceOpen={true} />
          </div>
        )}
      </main>

      {route.view !== "assistant" && (
        <ChatAssistant bundle={bundle} defects={defects} onSelectDefect={goToDefect} />
      )}

      <InterviewerGuideModal isOpen={showGuide} onClose={() => setShowGuide(false)} />

      <footer className="border-t border-line px-4 py-6 sm:px-6">
        <div className="mx-auto flex max-w-screen flex-wrap items-center justify-between gap-3 text-2xs text-ink-faint">
          {/* This used to read "no server, no environment variables", which
              stopped being true when the assistant became a real grounded
              Gemini call. Every VIEW is still pre-rendered; the single dynamic
              surface is the chat route. */}
          <span>
            All dashboard data pre-rendered from{" "}
            <code className="font-mono">public/data/{sourceFile}</code> at build time — no runtime
            fetches to render any view. The only server surface is{" "}
            <code className="font-mono">/api/chat</code>.
          </span>
          <span className="font-mono">python {bundle.run.python_version}</span>
        </div>
      </footer>
    </div>
  );
}
