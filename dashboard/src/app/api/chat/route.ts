/**
 * `/api/chat` — the only server route in this application.
 *
 * Its existence is the reason `next.config.ts` no longer sets
 * `output: "export"`. Everything else here is still statically pre-rendered at
 * build time; this one route runs on demand, because it holds a secret
 * (`GEMINI_API_KEY`) and a static site by definition cannot.
 *
 * The file is deliberately a thin adapter. All logic — validation, rate
 * limiting, retrieval, the upstream call, the error taxonomy — lives in
 * `src/lib/chatHandler.ts`, which a test can import without booting Next.
 * Next.js also restricts what a `route.ts` may export (HTTP verbs plus a fixed
 * set of segment-config constants), so there is nowhere here to put a testable
 * seam even if we wanted one.
 *
 *   GET  → { configured, model, bundleAvailable }   capability probe, no secrets
 *   POST → { ok: true, answer, … } | { ok: false, kind, message }
 */

import { handleChatPost, handleChatStatus } from "@/lib/chatHandler";

/**
 * Node runtime, not Edge. The handler reads the ~1 MB bundle from disk through
 * `node:fs` (see `src/lib/bundle.ts`), which the Edge runtime does not provide.
 */
export const runtime = "nodejs";

/**
 * Never pre-render or cache this route. The answer depends on the request body
 * and on an environment variable read at request time; a build-time evaluation
 * would bake in "not configured" forever on a deployment whose key is set in
 * the Vercel dashboard rather than at build.
 */
export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  return handleChatPost(request);
}

export function GET(): Response {
  return handleChatStatus();
}
