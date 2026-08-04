/**
 * Build-time bundle loader. SERVER ONLY — imports `node:fs`.
 *
 * Why read from disk instead of `import bundle from "./bundle.json"`:
 *   1. The real artefact (`bundle.json`) is produced by the Python pipeline and
 *      is gitignored. A static `import` would make `npm run build` fail on a
 *      clean checkout. Reading at runtime-of-build lets us fall back to the
 *      committed mock.
 *   2. It keeps the fallback decision explicit and reportable — the UI shows
 *      which file it rendered from, so nobody mistakes mock numbers for real
 *      ones.
 *
 * The page is a `force-static` Server Component, so this runs once during
 * `next build` and the parsed result is serialised into the HTML. The browser
 * performs zero data fetches to render the dashboard.
 *
 * It is ALSO called at request time by `/api/chat`, which needs the bundle to
 * ground the assistant's answers. That is the reason `next.config.ts` carries
 * an `outputFileTracingIncludes` entry for `public/data/*.json`: Next's tracer
 * cannot see through a runtime `readFileSync`, so the file has to be declared
 * or the serverless function ships without it.
 */

import fs from "node:fs";
import path from "node:path";

import { codeRefsFor } from "./grounding";
import type { Bundle, DefectView } from "./types";

export interface LoadedBundle {
  bundle: Bundle;
  /** "bundle.json" (real pipeline output) or "bundle.mock.json" (fallback). */
  sourceFile: string;
  isMock: boolean;
}

/**
 * Read the dashboard bundle, preferring the real pipeline output.
 *
 * Resolution order:
 *   1. `public/data/bundle.json`      — copied from `output/dashboard_bundle.json`
 *   2. `public/data/bundle.mock.json` — committed stand-in, same shape
 *
 * @throws If neither file exists. Failing the build is correct here: a
 *         dashboard with no data is worse than no dashboard.
 */
export function loadBundle(): LoadedBundle {
  const dataDir = path.join(process.cwd(), "public", "data");
  const real = path.join(dataDir, "bundle.json");
  const mock = path.join(dataDir, "bundle.mock.json");

  const chosen = fs.existsSync(real) ? real : mock;
  if (!fs.existsSync(chosen)) {
    throw new Error(
      `No data bundle found. Expected ${real} or ${mock}. ` +
        `Run the pipeline and copy output/dashboard_bundle.json into public/data/bundle.json.`,
    );
  }

  const bundle = JSON.parse(fs.readFileSync(chosen, "utf8")) as Bundle;
  const sourceFile = path.basename(chosen);
  return { bundle, sourceFile, isMock: sourceFile !== "bundle.json" };
}

/**
 * Join the catalog (what SHOULD exist) to the audit log (what the run FOUND)
 * and to the code index (where it is handled), producing the rows the Defect
 * Explorer and the coverage strip both render.
 *
 * The join is intentionally left-outer from the catalog: a defect class the
 * pipeline never reported must still appear, marked `missing`. Silence is the
 * failure mode this dashboard exists to make loud.
 */
export function buildDefectViews(bundle: Bundle): DefectView[] {
  const auditList: any[] = Array.isArray(bundle.audit)
    ? bundle.audit
    : (bundle.audit as any)?.records ?? [];
  const catalogList: any[] = Array.isArray(bundle.defect_catalog)
    ? bundle.defect_catalog
    : (bundle.defect_catalog as any)?.defects ?? [];

  const auditByCode = new Map(auditList.map((a: any) => [a.code, a]));

  return catalogList.map((spec: any) => {
    const audit = auditByCode.get(spec.code) ?? null;
    const detected = audit ? audit.detected_count : null;

    let coverage: DefectView["coverage"];
    if (!audit) {
      coverage = "missing";
    } else if (spec.expected_count === null) {
      // Variable-count defect: any detection at all is a pass, zero is not.
      coverage = detected && detected > 0 ? "match" : "mismatch";
    } else {
      coverage = detected === spec.expected_count ? "match" : "mismatch";
    }

    return {
      ...spec,
      audit,
      detected_count: detected,
      coverage,
      // `codeRefsFor` normalises the tag sites. The pipeline serialises them as
      // `{ file, line, snippet }` while `types.ts` — and `CodeViewer`, which
      // keys its file tabs off `ref.path` — expect `{ path, … }`. Reading the
      // raw array straight out of `code_index` therefore yields refs whose
      // `path` is `undefined`, and the code viewer falls through to its
      // "source_files does not carry it" empty state for every defect. One
      // normalisation, used by the UI and by the assistant's prompt builder
      // alike, so the two can never disagree about where a defect lives.
      refs: codeRefsFor(bundle, spec.code),
    };
  });
}

/**
 * Total dollar value of the preserved silent discount (TX-03).
 *
 * Pulled from the revenue_reconciliation metric rather than recomputed, so the
 * headline number on the Overview and the reconciliation table can never
 * disagree. Returns null if the metric is absent — the UI then renders an
 * explicit empty state instead of a zero, because "$0" and "unknown" are very
 * different claims.
 */
export function discountImpact(bundle: Bundle): number | null {
  const recon = bundle.analytics?.metrics?.revenue_reconciliation;
  if (!recon) return null;
  const row = recon.rows.find((r) =>
    String(r.line_item ?? "").toLowerCase().includes("discount"),
  );
  if (!row) return null;
  const amount = Number(row.amount);
  return Number.isFinite(amount) ? Math.abs(amount) : null;
}
