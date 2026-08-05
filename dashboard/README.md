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
| **Assistant** | A grounded Q&A panel. Retrieves the relevant slice of the bundle for your question and answers from that alone, citing `file:line`. Falls back to scripted, bundle-derived answers when no model is configured — and says which one you are reading. See [The assistant](#the-assistant). |

---

## Quick start

```bash
cd dashboard
npm install
npm run dev          # http://localhost:3000
```

Production build and run:

```bash
npm run build
npm start            # next start, http://localhost:3000
```

Type-check, and run the assistant's test suite:

```bash
npm run typecheck
npm test             # context selector + route handler, mocked upstream, no network
```

> **This is no longer a static export.** It was, until the assistant became real. The
> chat route holds an API key and a static site has nowhere to put one. Every *page* is
> still pre-rendered at build time — `next build` reports `/` as `○ (Static)` and only
> `/api/chat` as `ƒ (Dynamic)` — so the dashboard itself still performs zero data fetches
> in the browser. The full reasoning is in the header comment of `next.config.ts`.

---

## Deploy to Vercel

```bash
npm i -g vercel      # once
cd dashboard
vercel login
vercel link          # once, to associate the project
vercel deploy --prod
```

Set the project **Root Directory** to `dashboard`. `vercel.json` pins the framework and
the build/install commands and nothing else.

> **`outputDirectory` was removed from `vercel.json`, deliberately.** It used to say
> `"out"`, which was correct for a static export and is now actively wrong: there is no
> `out/` directory any more. Vercel's Next.js preset knows where a Next build puts its
> output and needs no help. A stale `outputDirectory` is what produced the
> `404: NOT_FOUND` on the previous deploy — Vercel looked for a folder the build never
> created and served nothing. If you see that 404 again, check this first.

### Setting `GEMINI_API_KEY` on Vercel

1. Vercel dashboard → your project → **Settings** → **Environment Variables**.
2. **Key**: `GEMINI_API_KEY`. **Value**: the key from
   [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
3. Tick the environments it applies to — **Production**, and Preview/Development if you
   want the assistant live there too.
4. Leave "Sensitive" on if offered; the value is never needed at build time, only at
   request time.
5. **Save**, then **redeploy** (Deployments → ⋯ → Redeploy). Environment variables are
   injected at deploy time; an existing deployment will not pick up a new one.

Do **not** name it `NEXT_PUBLIC_GEMINI_API_KEY`. Next.js inlines every `NEXT_PUBLIC_*`
variable into the JavaScript it sends to the browser, so on a public URL that is
publication, not configuration.

Locally, put it in `dashboard/.env.local` (gitignored; see `.env.example`):

```bash
echo 'GEMINI_API_KEY=your-key-here' >> dashboard/.env.local
```

**Without a key nothing breaks.** `GET /api/chat` reports `configured: false`, the panel
opens in offline mode, and every answer comes from the scripted set assembled out of
`bundle.json` — clearly labelled as scripted. Deploying with no key is a supported
configuration, not a degraded one.

---

## Where the data comes from

Everything the dashboard renders comes from **one JSON file**, read from disk at *build*
time by a Server Component (`src/lib/bundle.ts`) and serialised into the pre-rendered HTML.
The browser performs **zero** data fetches to render any view — there is no loading state
anywhere in the dashboard because there is nothing to load. (The assistant panel is the one
exception, and only when you ask it something: it posts to `/api/chat`, which reads the
same file server-side to ground its answer.)

Resolution order:

1. `public/data/bundle.json` — the real pipeline artefact.
2. `public/data/bundle.mock.json` — the committed stand-in. Same shape exactly.

Both are committed, so a clean checkout builds without running the pipeline first.

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

## The assistant

A grounded question-answering panel over the pipeline's own output. It replaces an earlier
"AI assistant" that contained no AI — it substring-matched a hand-written answer list — and
whose hand-written figures had gone stale (it quoted `$170,816.34` for a number that is
`$168,957.80`). Both problems are fixed structurally rather than by editing the strings.

### Two modes, always labelled

| Mode | When | What produces the text |
|---|---|---|
| **Live** | `GEMINI_API_KEY` is set and the server can read the bundle | `gemini-3.6-flash`, answering from a retrieved slice of the bundle |
| **Offline** | no key, the API call fails, or you clicked a bundle chip | Scripted answers assembled from `bundle.json` at render time |

The panel opens on **the ten hardest questions about this pipeline**, ranked, taken from
`INTERVIEW_QA.md` — four visible, six behind a disclosure, so a reviewer does not have to
guess what the assistant can do. Those chips ask their question through the normal path
(live model when configured, scripted answer when not); the collapsed bundle chips below
them are the always-scripted shortcuts. Free-text questions and `TX-03`-style deep links
work exactly as before.

Every assistant message carries a source badge, and offline answers say
*"offline mode — scripted answer"* with the reason in parentheses. There is no state in
which a reviewer cannot tell which mode produced what they are reading. That matters more
here than in most apps: the submission's whole claim is numerical trustworthiness, so an
unlabelled machine-written dollar figure would undermine the thing it is describing.

### How grounding works

The bundle is ~1.02 MB. It does not fit in a prompt and would not help there if it did.
`src/lib/grounding.ts` turns a question into a compact context:

1. **An always-on preamble** — row counts, warehouse counts, 17/17 coverage, and the full
   revenue reconciliation, on *every* request regardless of the question. Models invent
   numbers when they need one and do not have it; this ensures they always have it.
2. **Defect dossiers** — any code named in the question (`TX-03`) gets its full
   detection / decision / rationale plus its audit record. Others are scored by term
   overlap against the catalog text and included in summary form.
3. **Metric blocks** — definition note (the explicit numerator/denominator), declared
   column units, the SQL, and the result rows, row-capped and labelled as partial.
4. **Source windows** — real lines sliced out of `source_files` around each
   `# DEFECT: <CODE>` tag site, with line numbers, so `file:line` citations are checkable.

Retrieval is term-overlap with field weighting, not embeddings. The corpus is 25 documents
whose text already contains the reviewer's vocabulary almost verbatim; a vector index would
add a build step and a threshold to tune in exchange for nothing measurable at this scale.
It is deterministic, which is also what makes it testable offline.

### Alias expansion

Term overlap only works when the reviewer's vocabulary and the catalog's coincide, and
often they do not. *"How do you handle refunds?"* shares no content word with a dossier
that says "return transactions with negative quantity and amount", and before the alias
table that question retrieved **nothing but the preamble**. *"Why don't the numbers add
up?"* did retrieve TX-03, but ranked fourth, and the one source window in the context went
to the wrong defect.

`ALIAS_RULES` in `src/lib/grounding.ts` is a hand-authored table from natural phrasings to
defect codes and metric ids — *postal code / leading zero* → `ST-01`, *doesn't add up /
silent discount* → `TX-03` + `revenue_reconciliation`, *guest checkout / anonymous* →
`TX-06` + `top_customers_lifetime`, and so on. A table rather than a cleverer scorer
because a table is reviewable: you can read it and tell whether "guest checkout" reaches
TX-06, which is not true of a similarity threshold.

A hit adds **12** to the target's score — worth exactly two title-word matches, more than
any single incidental word overlap and less than an explicitly typed defect code, which
bypasses scoring entirely. The reasoning is argued in full above the table. The phrases
that fired are reported to the client and shown under "Grounding context used", so a
surprising retrieval can always be traced to the row that caused it.

The test suite asserts that each of the ten questions in `INTERVIEW_QA.md` retrieves the
defect classes and metrics its answer actually cites. All ten do.

### The numeric self-audit

The system instruction forbids stating a figure that is not in the context. Nothing
verified that, which made it a request rather than a control — and for a submission whose
argument is numerical trustworthiness, one invented dollar figure in front of a reviewer
discredits every figure that is right.

`src/lib/numericAudit.ts` checks every answer before it is rendered. It extracts each
numeric literal, normalises it (currency symbols, thousands separators, percent signs,
trailing zeros — `$158,044.29`, `158044.29` and `158,044.3` are one figure), and looks it
up in the material the answer was grounded on: the retrieved context for a live answer,
`bundle.json` for a scripted one. The badge says which.

A figure is **not** flagged when it is (1) not a claim about the data — a defect code, an
entity id, a date, a `file.py:214` citation; (2) a bare integer ≤ 12, which in English is
enumeration rather than measurement (`$5` and `5%` are still checked — a unit means a
measurement); (3) present in the context; or (4) equal to `a+b`, `a−b`, `a×b`, `a÷b` or
`100·a÷b` over figures **this answer itself shows and that already passed (3)**. Restricting
the operand pool to the answer's own verified figures — rather than to the whole context —
is what keeps derivation from becoming a loophole: hundreds of context numbers have enough
pairwise sums to excuse almost anything, while an answer has a handful and a reviewer can
check them by eye. Derived figures are reported as their own category, because they are a
weaker guarantee than "present".

What the badge claims is exactly what the check performs, and that limitation ships with
every result: **it proves a figure appears in the grounding material, not that it was used
correctly.** "Net revenue is $9,952.03" passes — that is the returns value. Pure,
deterministic, no network, ~0.1 ms per answer, and unit-tested both ways: a plausible but
absent figure must be flagged, and legitimate answers must not be.

Measured on the ten model answers in `INTERVIEW_QA.md`, audited against the context that
would really have been retrieved for each: **seven pass, three warn — and all three
warnings are correct.** They are `504` (a lineage-CSV row index), `14626` (the real ZIP for
Greece, NY) and `$79,000` (from a mutation-testing experiment). Every one of those is a
true fact the assistant could not have sourced from its grounding, which is precisely what
the check is for.

**The context budget is 9,000 tokens** (~36 KB, ~3.5% of the bundle) — see
`DEFAULT_CONTEXT_TOKEN_BUDGET` in `src/lib/grounding.ts`, where the choice is argued in
full. It is not a model limit; it is a cost, latency and precision budget. Blocks are
filled greedily in priority order and anything that does not fit is *named in the prompt*,
so the model can say "I would need the aov_by_region rows" instead of guessing.

The system instruction is a hard extraction contract: answer only from the context, never
state a number that is not in it, say so plainly when the answer is not there, cite
`file:line`. What the server retrieved is shown in the UI under "Grounding context used" —
retrieval you cannot inspect is indistinguishable from retrieval that did not happen.

### The API key

`GEMINI_API_KEY` is read from `process.env` in `src/lib/chatHandler.ts`, which only the
server-side `/api/chat` route imports. It is:

* never prefixed `NEXT_PUBLIC_`, so Next cannot inline it into a client bundle;
* never put in a URL — the Gemini REST API also accepts `?key=`, but the header
  `x-goog-api-key` keeps the secret out of redirect chains, proxy logs and error messages
  that echo the request URL;
* never in a response body — upstream failures are reduced to a status code and a fixed
  phrase, and a redaction pass runs over anything else that reaches the client;
* verified absent from the build: with the key set, a production build contains the value
  nowhere under `.next/` at all. The only thing the browser learns is the boolean from
  `GET /api/chat`.

### Cost and abuse controls, and their limits

This is a public URL spending someone's personal API quota. The route bounds every factor
of the bill: a **24 KB request body cap**, a **1,200-character question cap**, a
**6-turn conversation cap** (history is replayed every request, so its cost compounds), the
**9,000-token context budget**, a **1,400-token output cap**, a **25-second upstream
timeout**, and a **per-IP rate limit of 20 requests per 5 minutes**.

Stated honestly, because a control you have misjudged is worse than one you know is
partial: **the rate limiter is in-memory and therefore per-instance.** Vercel runs the
route as a serverless function and may run many concurrently, each with its own empty
counter; cold starts reset it; and the key is an IP from `x-forwarded-for`, which is shared
by NAT and rotatable by VPN. It stops one bored visitor holding down Enter. It is *not* a
quota guard. A real one needs a shared store (Vercel KV, Upstash), and the actual backstop
is a spend cap on the Google Cloud project that issued the key — set one. The reasoning for
accepting that trade-off is in `src/lib/rateLimit.ts`.

### Tests

```bash
npm test
```

161 assertions covering the context selector (determinism, budget enforcement, that it
quotes live figures and that none of the three stale figures can reappear), the alias table
(every ordinary phrasing lands on the right defect; all ten interview questions retrieve
what their answers cite), the numeric verifier (rounding tolerance, sign, percent-versus-
ratio, every exemption rule, the derivation rule and its anti-laundering guard, plus an
adversarial figure that must be flagged and nine legitimate answers that must not be), the
scripted answers, and the route handler against a **mocked** upstream: success, upstream
non-2xx, network failure, safety block, empty candidate, missing key, oversized body,
malformed JSON, history cap and the rate limiter. Two of those assertions specifically
check that a key never escapes — one feeds an upstream error body containing the key, one
throws a network error containing it.

**One assertion fails on this bundle, deliberately.** `revenue_reconciliation` emits a
single `reconciliation_delta` column, while this dashboard and `INTERVIEW_QA.md` both
describe two independent controls, `line_level_delta` and `aggregate_delta`. The dashboard
renders the two missing figures as "not in bundle" — never as `$0.00` — and quotes the one
that exists under its real name. It could be made green in one line by reading
`reconciliation_delta` into both fields, and that is exactly the line this project exists
to argue against: it would report that two independent controls passed when one ran. The
fix belongs in `src/analytics/queries.py`; the assertion stays red until it lands.

**No test calls Gemini.** The build sandbox has no route to the API, and a test that faked
a successful call would be exactly the dishonesty this project argues against. The one
thing only a live deployment can confirm is that the real endpoint accepts this exact
request shape.

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

Deliberately constants rather than environment variables: a reviewer should be able to see
the configuration by reading one short file, and none of it is secret — these values are
compiled into the client bundle and are public by construction.

The project's one genuine secret, `GEMINI_API_KEY`, is therefore **not** in `config.ts`.
The rule that separates them: if it may appear in the browser's view-source it belongs in
`config.ts`; if it may not, it must never be imported by a `"use client"` module.

---

## Design decisions worth knowing

**Pre-rendered pages, one dynamic route.** A review artefact should have as close to zero
operational surface as its features allow. Every page is still generated at build time from
the bundle on disk; the only thing that runs per request is `/api/chat`, which exists
because it holds a secret. If the assistant were removed, `output: "export"` could go
straight back into `next.config.ts` and nothing else would change.

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
├── next.config.ts              # why this is no longer a static export
├── tailwind.config.ts          # palette: one accent + four severity colours
├── vercel.json                 # framework + commands only; NO outputDirectory
├── .env.example                # GEMINI_API_KEY placeholder, no values
├── public/data/
│   ├── bundle.mock.json        # committed stand-in (generated, see above)
│   └── bundle.json             # real pipeline output
├── scripts/
│   └── generate_mock_bundle.py # rebuilds the mock from data/raw/*.csv
├── tests/
│   ├── chat.test.ts            # selector + handler, mocked upstream, no network
│   └── tsconfig.json           # emits CommonJS to .test-build/ for plain node
└── src/
    ├── app/                    # layout, the page, globals.css
    │   └── api/chat/route.ts   # the only server route: thin adapter over chatHandler
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
    │   ├── ChatAssistant.tsx   # the assistant panel; live + offline, always labelled
    │   └── ui.tsx              # shared primitives
    └── lib/
        ├── types.ts            # THE bundle contract
        ├── bundle.ts           # loader + catalog×audit join
        ├── config.ts           # every tunable public constant
        ├── format.ts           # null-safe formatters
        ├── schema.ts           # star schema model (hand-authored)
        ├── lineage.ts          # pipeline stage graph (hand-authored)
        ├── grounding.ts        # retrieval: run facts, alias table, scoring, budget, prompt
        ├── numericAudit.ts     # post-response figure check; pure, both sides of the wire
        ├── presets.ts          # scripted answers + the ten ranked interview questions
        ├── chatContract.ts     # client↔server wire types (safe for the browser)
        ├── chatHandler.ts      # SERVER ONLY: validation, limits, upstream call
        └── rateLimit.ts        # per-IP limiter, and an honest note on what it misses
```

`schema.ts` and `lineage.ts` are hardcoded on purpose: they are architecture, not pipeline
output. Reflecting them out of SQLite would lose the column-level *notes*, which are the
part a reviewer actually cares about.
