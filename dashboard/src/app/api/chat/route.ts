/**
 * The only server route in the app: the grounded reviewer's assistant.
 *
 * This file is deliberately a thin adapter. Next.js validates the export
 * surface of a `route.ts` — HTTP verbs and a fixed set of segment-config
 * constants, nothing else — so all of the logic (validation, limits, retrieval,
 * the upstream call, the error taxonomy and the numeric self-audit) lives in
 * `src/lib/chatHandler.ts`, where a test can import it without booting Next.
 * That is the only way the failure paths are exercisable from a sandbox with no
 * route to the model API. See `tests/chat.test.ts`.
 *
 * WHY THIS FILE WAS REWRITTEN
 * ---------------------------
 * It previously carried a second, older implementation of the assistant: a
 * `@google/genai` streaming call against `gemini-2.5-flash`, grounded on a
 * hand-typed six-line summary of the pipeline with the run's dollar figures
 * pasted into a template literal. Three problems, in ascending order of
 * seriousness:
 *
 *   1. It did not implement the contract the browser speaks. `ChatAssistant`
 *      probes `GET /api/chat` for `{ configured, model, bundleAvailable }` and
 *      POSTs `{ question, history }` expecting a JSON `ChatResponse`. This file
 *      exported no GET, and read `message` from the body. Every live request
 *      therefore failed and the panel fell back to offline mode permanently —
 *      the entire grounded path was dead code in the deployed artefact.
 *   2. Its figures were typed in by hand, which is precisely the defect that
 *      `presets.ts` was restructured to eliminate: hand-typed numbers are
 *      correct only until the pipeline is re-run.
 *   3. Grounding on a six-line summary is not grounding. Any question outside
 *      those six lines was answered from the model's own memory, with nothing
 *      to check it against.
 *
 * `chatHandler.ts` and `README.md` both already described this file as a thin
 * adapter over the handler. It now is one.
 *
 * `GEMINI_API_KEY` is read inside `chatHandler`, from `process.env`, on the
 * server only. It is never named with a `NEXT_PUBLIC_` prefix, never placed in
 * a URL, and never included in a response body.
 */

import { handleChatPost, handleChatStatus } from "@/lib/chatHandler";

/**
 * Never statically evaluated at build time. The route reads an environment
 * variable and a per-request body; `next build` must report it as `ƒ (Dynamic)`
 * rather than folding a build-time answer into the static export.
 */
export const dynamic = "force-dynamic";

/**
 * Capability probe and diagnosis surface. Returns a boolean about the key,
 * never the key — plus the resolution state of this instance: which ENDPOINT
 * the deployment is able to use (the Interactions API or the legacy
 * generateContent path), which model is answering, what else is queued, whether
 * ListModels has run, and what has already been retired. That first field is
 * why this probe earns its keep: a deployment where every call returned HTTP
 * 400 could not, from the outside, distinguish a bad key from an endpoint that
 * refuses this kind of key. Synchronous and network-free: it reports what the
 * instance knows, and never spends an upstream call to learn more, because a
 * reviewer who opens the panel and closes it again should not cost the
 * deployment a round-trip.
 */
export function GET(): Response {
  return handleChatStatus();
}

/** The grounded answer. Everything of substance is in `chatHandler`. */
export function POST(request: Request): Promise<Response> {
  return handleChatPost(request);
}
