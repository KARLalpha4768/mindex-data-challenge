# Data Quality Review Dashboard

A static review dashboard for the Mindex data-engineering code challenge. It exists to
answer one question fast: **did this pipeline find every seeded defect, what did it decide
to do about each one, and where exactly is the code that does it?**

Built to be read in about eight minutes and to let a reviewer drill from any finding down
to the exact tagged line of Python that handles it.

---

## What it shows

| View | What it answers |
|---|---|
| **Overview** | Did the run reconcile? Headline counters, plus a coverage strip proving `detected == expected` for all 17 defect classes. A red cell means the pipeline's own assertion failed. |
| **Defect Explorer** | The centrepiece. Filter and sort all 17 defect classes; select one to see detection method, decision, rationale, expected vs detected counts, affected business keys, and a syntax-highlighted code viewer **scrolled to and highlighting the exact lines** tagged `# DEFECT: <CODE>`. Copy-permalink and an out-link to the GitHub blob. |
| **Data Profile** | Per-dataset, per-column census of the RAW files taken *before* cleaning: null bars, dtype badges, distinct counts, ranges, samples. |
| **Lineage** | Raw CSV → profile → clean → star schema → analytics. Every stage lists the defect codes it owns; selecting one opens the Defect Explorer pre-filtered to those codes. |
| **Schema** | The star schema with the grain stated in words on every table, PK/FK/NK badges, and column-level notes linking back to the defect each column exists to expose. |
| **Analytics** | One card per metric: the explicit numerator/denominator, the raw SQL in a collapsible highlighted block, the result rows, plus a MoM revenue chart and a return-rate chart with the 10% alert threshold drawn as a reference line. |

---

## Quick start

```bash
cd dashboard
npm install
npm run dev          # http://localhost:3000
```

Production build (static export into `out/`):

```bash
npm run build
npx serve out        # or: npm start
```

Type-check without building:

```bash
npm run typecheck
```

---

## Deploy to Vercel

The app is a pure static export (`output: "export"` in `next.config.ts`). No server, no API
routes, no environment variables, no runtime data fetching.

```bash
npm i -g vercel      # once
cd dashboard
vercel login
vercel link          # once, to associate the project
vercel deploy --prod
```

`vercel.json` pins `outputDirectory: "out"` and the build/install commands, so a
Git-connected import works with zero dashboard configuration — set the project **Root
Directory** to `dashboard` and nothing else.

Any static host works equally well: `out/` is plain HTML, CSS and JS.

---

## Where the data comes from

Everything the dashboard renders comes from **one JSON file**, read from disk at *build*
time by a Server Component (`src/lib/bundle.ts`) and serialised into the pre-rendered HTML.
The browser performs **zero** data fetches — there is no loading state anywhere in the app
because there is nothing to load.

Resolution order:

1. `public/data/bundle.json` — the real pipeline artefact. **Gitignored**, so a clean
   checkout never carries stale run output.
2. `public/data/bundle.mock.json` — the committed stand-in. Same shape exactly.

When the mock is used, a warning banner is rendered across the top of the app. A reviewer
can never mistake stand-in data for real pipeline output.

### Wiring in the real bundle

```bash
# from the repo root, after running the pipeline
cp solution/output/dashboard_bundle.json dashboard/public/data/bundle.json
cd dashboard && npm run build
```

That is the whole integration. No schema migration, no env var, no rebuild of the mock.

### About `bundle.mock.json`

It is not fabricated. `scripts/generate_mock_bundle.py` computes every count, every
affected business key, every dollar figure directly from the real `solution/data/raw/*.csv`
files, using the same detection predicates the pipeline uses:

```bash
python3 dashboard/scripts/generate_mock_bundle.py   # run from the repo root
```

Hand-authored parts, stated plainly:

* **`defect_catalog` prose** (title / detection / decision / rationale) — mirrors the
  contract's defect table.
* **`source_files`** — representative excerpts of the cleaning modules, written to the
  project's annotation standard so the code viewer has real, tagged Python to display. The
  `code_index` line numbers are *derived* from those excerpts by the same
  `# DEFECT: <CODE>` grep the real pipeline performs, so the highlighting is genuinely
  correct rather than hardcoded.

Verified figures currently in the mock: 505 raw transaction rows → 474 loaded; 16 → 15
stores; 32 → 30 products; **$961.48** of silent discount (TX-03) preserved rather than
recomputed away; all 17 defect classes reconciling detected-vs-expected.

---

## Bundle shape

`src/lib/types.ts` is the single source of truth for the pipeline ↔ dashboard interface.
If the Python side changes a key, TypeScript should break there first.

```ts
{
  run:            { generated_at, as_of_date, python_version,
                    row_counts: { raw: {...}, clean: {...} } },
  defect_catalog: [ { code, dataset, title, severity, expected_count,
                      detection, decision, rationale, source_ref } ],
  audit:          [ { code, detected_count, action, affected_keys[], notes } ],
  profiling:      { <dataset>: { row_count, duplicate_row_count,
                    columns: [ { name, dtype, null_count, null_pct,
                                 distinct_count, min, max, sample_values[] } ] } },
  analytics:      { metrics: { <metric_id>: { title, description, sql_ref,
                                              definition_note, sql, rows[] } } },
  code_index:     { <DEFECT_CODE>: [ { path, line, snippet } ] },
  source_files:   { <path>: { lines: string[], language } }
}
```

Notes for whoever emits this:

* `analytics.metrics.<id>.sql` **must be present** — the Analytics view renders the literal
  SQL. `output/analytics.json` per the contract carries only `sql_ref`; the dashboard
  bundle needs the query text inlined as well.
* `code_index[code][].line` is **1-based** and must index into
  `source_files[path].lines` (i.e. line *N* is `lines[N-1]`).
* `source_files` should carry **whole files**, not fragments — the viewer shows surrounding
  context and the line numbers must match the real file for the GitHub out-links to land
  correctly.
* `run.row_counts.raw` and `.clean` should use the **same dataset keys** so the Overview
  can pair them.
* `expected_count: null` is allowed and renders as `var`; any non-zero detection then
  counts as coverage.
* A defect class present in `defect_catalog` but absent from `audit` renders as **"not
  reported"** in red — silence is treated as failure, not as a pass.

Two constants are **not** carried in the bundle and live in `src/lib/config.ts` instead:
the GitHub base URL and `RETURN_RATE_ALERT_THRESHOLD` (mirrored from `src/config.py`).

---

## Configuration

Everything a fork would need to change is in `src/lib/config.ts`:

```ts
export const GITHUB_BASE_URL =
  "https://github.com/mindex-challenge/data-engineer-challenge/blob/main";
export const REPO_SOURCE_PREFIX = "solution";
export const RETURN_RATE_ALERT_THRESHOLD = 0.1;
```

Deliberately constants rather than environment variables: with `output: "export"` there is
no server to read env at runtime, and a reviewer should be able to see the configuration by
reading one short file.

---

## Design decisions worth knowing

**Static export, not SSR.** A review artefact should have zero operational surface. `next
build` emits `out/`; it deploys anywhere and cannot break at runtime.

**Hash routing, one route.** Six views addressed by URL hash (`#defects/TX-03`,
`#defects/codes:TX-01,TX-02`), so every view and every individual defect is linkable and
back-button friendly without generating a separate HTML document per defect.

**`prism-react-renderer`, not `shiki`.** shiki produces better colour but is a build-time
tool: tokenising every source file during the build and serialising the token trees into
static HTML costs several hundred KB for a reader who will open two or three files, and
running shiki in the browser means shipping its WASM regex engine plus grammars.
`prism-react-renderer` is ~10KB, bundles the Python and SQL grammars, and — the deciding
factor — hands us tokens **per line**, which the code viewer needs anyway for line numbers,
the highlight band and the scroll target. The full argument is in the header comment of
`src/components/CodeViewer.tsx`.

**Comments are not dimmed in the code viewer.** In this codebase the comments carry the
reasoning a reviewer came to read, so they get near-body contrast and keywords stay
subordinate. That is the opposite of a normal editor theme, on purpose.

**`null` never renders as `0`.** Everything in `src/lib/format.ts` renders absent values as
an em-dash. An analytics table showing `0%` where the denominator was NULL is exactly the
class of lie this project is about catching.

**Accessibility.** Semantic tables with `<caption>` and `<th scope>`, real `<a>`/`<button>`
elements throughout, `aria-sort` on sortable headers, `<details>` for disclosures, a skip
link, focusable scroll regions, a visible focus ring that is never removed, and
`prefers-reduced-motion` honoured. Responsive down to tablet; the Defect Explorer stacks
its table and detail panel below 1280px.

---

## Layout

```
dashboard/
├── next.config.ts              # output: "export"
├── tailwind.config.ts          # palette: one accent + four severity colours
├── vercel.json
├── public/data/
│   ├── bundle.mock.json        # committed stand-in (generated, see above)
│   └── bundle.json             # real pipeline output (gitignored)
├── scripts/
│   └── generate_mock_bundle.py # rebuilds the mock from data/raw/*.csv
└── src/
    ├── app/                    # layout, single route, globals.css
    ├── components/
    │   ├── Dashboard.tsx       # shell + hash router
    │   ├── Overview.tsx
    │   ├── DefectExplorer.tsx  # table + filters + sort
    │   ├── DefectDetail.tsx    # decision record
    │   ├── CodeViewer.tsx      # scrolls to and highlights tagged lines
    │   ├── DataProfile.tsx
    │   ├── Lineage.tsx
    │   ├── SchemaView.tsx
    │   ├── Analytics.tsx
    │   ├── MetricCharts.tsx    # recharts: MoM + return rate w/ threshold
    │   ├── SqlBlock.tsx
    │   └── ui.tsx              # shared primitives
    └── lib/
        ├── types.ts            # THE bundle contract
        ├── bundle.ts           # build-time loader + catalog×audit join
        ├── config.ts           # every tunable constant
        ├── format.ts           # null-safe formatters
        ├── schema.ts           # star schema model (hand-authored)
        └── lineage.ts          # pipeline stage graph (hand-authored)
```

`schema.ts` and `lineage.ts` are hardcoded on purpose: they are architecture, not pipeline
output. Reflecting them out of SQLite would lose the column-level *notes*, which are the
part a reviewer actually cares about.
