import type { NextConfig } from 'next';

/**
 * WHY THIS FILE CARRIES A FILE-TRACING RULE.
 *
 * Every VIEW in this dashboard is pre-rendered at build time. The single dynamic
 * surface is `/api/chat`, and it reads two artefacts from disk AT REQUEST TIME:
 *
 *   public/data/bundle.json    — the pipeline's own output, read by `bundle.ts`;
 *                                without it nothing can be grounded.
 *   public/data/csv_diff.json  — the raw-versus-clean cell diff, read by
 *                                `csvDiff.ts`; it is what turns the coordinates
 *                                of a clicked cell ({ dataset, rowIndex,
 *                                column }) into the row the model is shown.
 *
 * Both are reached through `fs.readFileSync(path.join(process.cwd(), …))`, and
 * Next's output file tracer works by following static `import`s. A runtime path
 * built from `process.cwd()` is not an import, so nothing in the source
 * *declares* that the serverless function needs these files.
 *
 * WHAT WAS ACTUALLY MEASURED, because the honest version of this comment matters
 * more than the reassuring one: on Next 15.5.22 the trace manifest
 * (`.next/server/app/api/chat/route.js.nft.json`) lists all three files in
 * `public/data/` WITH OR WITHOUT the rule below — the `public` directory is
 * carried into the deployment by other means. So this rule is not currently
 * load-bearing, and it is not claimed to be.
 *
 * It is kept for two reasons anyway. First, `bundle.ts` has always documented
 * this dependency as declared here, and a comment that describes a rule which
 * does not exist is worse than no comment. Second, the behaviour it relies on is
 * an implementation detail of one Next version: the day it changes, the failure
 * is invisible locally (the files are right there under `next start`) and total
 * in production — `bundle_unavailable` on every question, and every cell
 * selection silently resolving to nothing. Declaring the dependency costs one
 * line and removes that class of failure from the table.
 *
 * The glob is `public/data/*.json` rather than two named files: both artefacts
 * are read the same way, from the same directory, by the same route, and a third
 * one added later must not have to remember to come back and edit this line.
 * The path is relative to the project root (this file's directory).
 */
const nextConfig: NextConfig = {
  outputFileTracingIncludes: {
    '/api/chat': ['./public/data/*.json'],
  },
};

export default nextConfig;
