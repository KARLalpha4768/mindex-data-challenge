"use client";

import React from "react";

import Analytics from "@/components/Analytics";
import ChatAssistant from "@/components/ChatAssistant";
import DataProfile from "@/components/DataProfile";
import DefectExplorer from "@/components/DefectExplorer";
import InterviewerGuideModal from "@/components/InterviewerGuideModal";
import Lineage from "@/components/Lineage";
import Overview from "@/components/Overview";
import RawVsCleanInspector, { type InspectorSelection } from "@/components/RawVsCleanInspector";
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
 *     #profile/dataset:stores   -> Data Profile, stores in focus
 *     #analytics/metric:return_rate_by_store -> Analytics, that card in focus
 *
 * Hash rather than the Next router because `output: "export"` produces static
 * files: a real route change would need a separate HTML document per defect,
 * and hash changes cost no navigation at all. It also means a copied permalink
 * works from `file://`.
 *
 * THIS COMPONENT IS ALSO THE ASSISTANT'S SENSE OF PLACE
 * ----------------------------------------------------
 * The route state below is exactly what the grounded assistant needs in order to
 * answer "what does this chart show?" — so it is handed to `ChatAssistant` as a
 * structured `viewContext` prop rather than being re-derived there from
 * `window.location` or read out of the DOM. One parser, one owner. The reasoning
 * is written out in full in `chatContract.ts`; the practical consequence is that
 * a child view holding focus state of its own (the Raw vs Clean inspector's
 * dataset switch) reports it UP to this component, instead of the chat panel
 * reaching sideways for it.
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
  /** A dataset in focus, from `#profile/dataset:stores`. */
  dataset: string | null;
  /** A metric in focus, from `#analytics/metric:return_rate_by_store`. */
  metric: string | null;
}

const DEFAULT_ROUTE: Route = {
  view: "overview",
  defect: null,
  codeFilter: null,
  dataset: null,
  metric: null,
};

const VALID_VIEWS = new Set<string>(VIEWS.map((v) => v.id));

/* The nav renders two weights. Split once here rather than filtering inside the
 * render, and deliberately as `=== "core"` / `!== "core"` so the two lists are a
 * partition of `VIEWS` by construction: a view added later with a group nobody
 * remembered to handle appears in the detail row rather than disappearing from
 * the header. A tab that exists but cannot be reached is the one outcome this
 * grouping must not be able to produce. */
const CORE_VIEWS = VIEWS.filter((v) => v.group === "core");
const DETAIL_VIEWS = VIEWS.filter((v) => v.group !== "core");

function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, "");
  if (!raw) return DEFAULT_ROUTE;

  const [viewPart, ...rest] = raw.split("/");
  if (!VALID_VIEWS.has(viewPart)) return DEFAULT_ROUTE;

  const view = viewPart as ViewId;
  const param = rest.join("/");
  if (!param) return { ...DEFAULT_ROUTE, view };

  if (param.startsWith("codes:")) {
    const codes = param
      .slice("codes:".length)
      .split(",")
      .map((c) => c.trim().toUpperCase())
      .filter(Boolean);
    return { ...DEFAULT_ROUTE, view, codeFilter: codes.length ? codes : null };
  }

  /* `dataset:` and `metric:` are additive prefixes. They cannot collide with the
   * bare-defect form below, because a defect code never contains a colon — so
   * every hash that worked before this change still parses to the same route. */
  if (param.startsWith("dataset:")) {
    const dataset = param.slice("dataset:".length).trim().toLowerCase();
    return { ...DEFAULT_ROUTE, view, dataset: dataset || null };
  }

  if (param.startsWith("metric:")) {
    const metric = param.slice("metric:".length).trim().toLowerCase();
    return { ...DEFAULT_ROUTE, view, metric: metric || null };
  }

  return { ...DEFAULT_ROUTE, view, defect: param.toUpperCase() };
}

export function buildHash(route: Partial<Route> & { view: ViewId }): string {
  if (route.defect) return `#${route.view}/${route.defect}`;
  if (route.codeFilter?.length) return `#${route.view}/codes:${route.codeFilter.join(",")}`;
  if (route.dataset) return `#${route.view}/dataset:${route.dataset}`;
  if (route.metric) return `#${route.view}/metric:${route.metric}`;
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

  /**
   * The dataset the Raw vs Clean inspector is showing.
   *
   * That switch is the inspector's own state and belongs there — it is not
   * addressable, it changes several times a minute while someone reads a diff,
   * and putting it in the hash would fill the back button with noise. But the
   * assistant still needs to know which table is on screen, so the inspector
   * reports it upward through a callback. The alternative (the chat panel
   * querying the DOM for the highlighted button) would make grounding depend on
   * markup, which is the failure mode this whole prop chain exists to avoid.
   */
  const [rawDataset, setRawDataset] = React.useState<string | null>(null);

  /**
   * The cell the reviewer has clicked in that inspector, or null.
   *
   * Reported upward by the same mechanism and for the same reason as the dataset
   * above: it is the inspector's own state, it is not addressable, and the chat
   * panel must not go looking for it in the DOM.
   *
   * `setRawCellSelection` is passed straight down as the callback. That is
   * deliberate — a `useState` setter has a stable identity for the life of the
   * component, so the effect in the inspector that reports the selection does
   * not re-fire on every render of this shell.
   */
  const [rawCellSelection, setRawCellSelection] = React.useState<InspectorSelection | null>(null);
  const [isAssistantOpen, setIsAssistantOpen] = React.useState(false);

  const openAssistantForCell = React.useCallback(() => {
    setIsAssistantOpen(true);
  }, []);

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

  /* Plain view navigation, for the orientation panel on the Overview. It goes
   * through `navigate` rather than being left to the anchor's own href so the
   * scroll position resets — landing halfway down the Raw vs Clean inspector
   * because the Overview happened to be scrolled there is disorienting in a way
   * that reads as a bug. The anchors keep their hrefs regardless, so
   * middle-click and "open in new tab" still work. */
  const goToView = React.useCallback((view: ViewId) => navigate({ view }), [navigate]);

  /**
   * What the assistant is told about where the reviewer is.
   *
   * HOOK ORDER: this `useMemo` — like every hook in this component — sits above
   * the single `return` below and above every conditional in the JSX. A hook
   * after an early return runs on some renders and not others, which is React
   * error #310 and shows up in production as a blank page reading "Application
   * error: a client-side exception has occurred". This file has no early return
   * and must not grow one above this line.
   *
   * The dataset comes from the hash when a permalink pinned one, otherwise from
   * whatever the Raw vs Clean inspector last reported — and only while that view
   * is the one on screen, because a stale dataset from a view nobody is looking
   * at would be a confident lie about the reviewer's position.
   */
  const viewContext = React.useMemo(
    () => ({
      view: route.view,
      defect: route.defect,
      codeFilter: route.codeFilter,
      dataset: route.dataset ?? (route.view === "raw" ? rawDataset : null),
      metric: route.metric,
      /**
       * The clicked cell, as COORDINATES ONLY.
       *
       * Note what is NOT here: `rawCellSelection.codes`. This object is
       * serialised verbatim into the POST body, so it carries the three fields
       * the server can validate against `csv_diff.json` — dataset, row index,
       * column — and nothing else. The server reads the row itself; the client
       * never tells it what a cell contains. (`codes` is passed to the panel
       * separately, below, and is used only to pick an offline answer.)
       *
       * Gated on the raw view being the one on screen, for the same reason
       * `dataset` is: a coordinate from a table nobody is looking at would be a
       * confident lie about where the reviewer is.
       */
      selection:
        route.view === "raw" && rawCellSelection
          ? {
              dataset: rawCellSelection.dataset,
              rowIndex: rawCellSelection.rowIndex,
              column: rawCellSelection.column,
            }
          : null,
    }),
    [
      route.view,
      route.defect,
      route.codeFilter,
      route.dataset,
      route.metric,
      rawDataset,
      rawCellSelection,
    ],
  );

  /**
   * The defect codes on the selected row. CLIENT-ONLY, and kept out of
   * `viewContext` on purpose (see above) — its single job is to let the panel
   * pick a useful scripted answer when no model is configured, so that clicking
   * a cell and asking about it still names the defect class and its decision on
   * a deployment with no API key.
   */
  const selectionCodes = React.useMemo(
    () => (route.view === "raw" ? rawCellSelection?.codes ?? [] : []),
    [route.view, rawCellSelection],
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
                className="rounded border border-accent/40 bg-accent/10 px-2.5 py-1 font-mono text-2xs font-semibold text-accent transition-colors hover:bg-accent/20"
              >
                Interviewer guide
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
              bar. The hashchange listener does the rest.

              TWO WEIGHTS, NOT TWO MENUS. The four "core" tabs are the route
              through the submission; the five "detail" tabs are the supporting
              evidence and sit after a separator at a smaller size. Nothing is
              hidden — every tab is one click and one Tab-key press away, in
              source order, with the same `aria-current` — but a reviewer with
              eight minutes can now see which four to spend them on without
              reading all nine labels. The grouping itself is declared in
              `config.ts`; see the comment there for why. */}
          <nav aria-label="Dashboard sections" className="-mb-px flex items-stretch gap-1 overflow-x-auto">
            {CORE_VIEWS.map((v) => {
              const active = route.view === v.id;
              return (
                <a
                  key={v.id}
                  href={`#${v.id}`}
                  aria-current={active ? "page" : undefined}
                  className={`whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
                    active
                      ? "border-accent text-ink"
                      : "border-transparent text-ink-dim hover:border-line-strong hover:text-ink"
                  }`}
                >
                  {v.label}
                </a>
              );
            })}

            {/* Separator, not a heading: it is decoration for sighted users and
                nothing at all for a screen reader, which reads the nav as one
                flat list of nine links either way. */}
            <span aria-hidden="true" className="my-2 w-px shrink-0 self-center bg-line" />
            <span
              aria-hidden="true"
              className="hidden shrink-0 self-center pl-2 pr-1 text-2xs uppercase tracking-wider text-ink-faint sm:inline"
            >
              detail
            </span>

            {DETAIL_VIEWS.map((v) => {
              const active = route.view === v.id;
              return (
                <a
                  key={v.id}
                  href={`#${v.id}`}
                  aria-current={active ? "page" : undefined}
                  className={`whitespace-nowrap border-b-2 px-2.5 py-2 text-xs transition-colors ${
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
            onSelectView={goToView}
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

        {route.view === "profile" && <DataProfile bundle={bundle} focusDataset={route.dataset} />}

        {route.view === "lineage" && (
          <Lineage bundle={bundle} defects={defects} onSelectCodes={goToCodes} />
        )}

        {route.view === "schema" && <SchemaView onSelectDefect={goToDefect} />}

        {route.view === "analytics" && <Analytics bundle={bundle} focusMetric={route.metric} />}

        {route.view === "tests" && <TestResults bundle={bundle} />}

        {route.view === "raw" && (
          <RawVsCleanInspector
            bundle={bundle}
            onSelectDefect={goToDefect}
            onDatasetChange={setRawDataset}
            onCellChange={setRawCellSelection}
            onOpenAssistant={openAssistantForCell}
          />
        )}

        {route.view === "assistant" && (
          <div className="mx-auto max-w-5xl">
            <ChatAssistant
              bundle={bundle}
              defects={defects}
              onSelectDefect={goToDefect}
              viewContext={viewContext}
              selectionCodes={selectionCodes}
              forceOpen={true}
            />
          </div>
        )}
      </main>

      {route.view !== "assistant" && (
        <ChatAssistant
          bundle={bundle}
          defects={defects}
          onSelectDefect={goToDefect}
          viewContext={viewContext}
          selectionCodes={selectionCodes}
          isOpen={isAssistantOpen}
          onToggleOpen={setIsAssistantOpen}
        />
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
