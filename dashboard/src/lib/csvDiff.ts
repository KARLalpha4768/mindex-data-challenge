/**
 * Server-side loader for `public/data/csv_diff.json`. SERVER ONLY — imports
 * `node:fs`, exactly as `bundle.ts` does, and for the same reason.
 *
 * WHY THE SERVER READS THIS FILE AT ALL
 * -------------------------------------
 * The browser already fetches it: `RawVsCleanInspector` pulls it at runtime to
 * render the raw-versus-clean tables. The server needs it for a different
 * reason. When a reviewer clicks a red cell and asks "why is this cell red?",
 * the client sends the assistant three COORDINATES — dataset, row index, column
 * — and nothing else. The server resolves those into the actual row by reading
 * this file itself.
 *
 * That split is deliberate and it is a security property, not a convenience:
 * anything the client puts in the request body is attacker-controlled text that
 * would end up inside the model's prompt on a public, unauthenticated URL.
 * Resolving the content server-side from a file this deployment shipped removes
 * that channel entirely. The full argument is in `chatContract.ts` under
 * `CellSelection`; the validation is in `grounding.ts:resolveCellSelection`.
 *
 * FAILURE POSTURE — identical to `loadBundle`'s, on purpose. A missing or
 * unparseable diff file DEGRADES: `null` is returned, the selection resolves to
 * nothing, and the question is answered without cell context. It does not throw,
 * because the assistant answers fifty other kinds of question that have no need
 * of this file, and taking the route down for all of them because one artefact
 * is absent would be a strictly worse outcome than an answer that says less.
 * (`loadBundle` throws when the BUNDLE is missing precisely because nothing can
 * be grounded without it. That asymmetry is the point.)
 *
 * DEPLOYMENT NOTE. Nothing in the source declares that the `/api/chat` function
 * needs this file — the path is built at runtime from `process.cwd()`, which
 * Next's import-following tracer cannot see. `public/data/*.json` is therefore
 * declared explicitly in `next.config.ts:outputFileTracingIncludes`, a glob that
 * covers this file as well as `bundle.json`: both are read the same way, from
 * the same directory, by the same route. (That file records what was actually
 * measured about the tracer's current behaviour, which is not the same thing as
 * what can be relied on.)
 */

import fs from "node:fs";
import path from "node:path";

import type { CsvDiff } from "./types";

/**
 * Read the raw-versus-clean cell diff, or `null` if it is not there.
 *
 * Never throws. The three ways this can fail — no file, unreadable file,
 * malformed JSON — are all deployment faults rather than request faults, and all
 * three produce the same honest degradation: the assistant answers without the
 * clicked cell and says nothing about it.
 */
export function loadCsvDiff(): CsvDiff | null {
  const file = path.join(process.cwd(), "public", "data", "csv_diff.json");
  try {
    if (!fs.existsSync(file)) return null;
    const parsed = JSON.parse(fs.readFileSync(file, "utf8")) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    return parsed as CsvDiff;
  } catch {
    // Deliberately no error text: this runs on a request path, and the only
    // thing a caller may learn is that the file is unavailable.
    return null;
  }
}
