import type { NextConfig } from "next";

/**
 * WHY THIS IS NO LONGER A STATIC EXPORT
 * -------------------------------------

 *
 * It stopped being the right call when the assistant became real. `/api/chat`
 * holds a secret — `GEMINI_API_KEY` — and a static export has no server, so
 * there is nowhere for a secret to live. The only alternative would be shipping
 * the key to the browser, which on a public URL means publishing it.
 *
 * What is *not* lost: every page in this app is still statically pre-rendered
 * at build time. `src/app/page.tsx` is a Server Component marked `force-static`
 * that reads the bundle from disk during `next build` and serialises it into
 * the HTML. The browser still performs zero data fetches to render the
 * dashboard. The only dynamic surface in the whole deployment is one POST
 * route that a reviewer has to click a button to reach.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,

  /**
   * No `next/image` usage in this app; kept so that adding one later cannot
   * silently introduce an image-optimisation dependency on the host.
   */
  images: { unoptimized: true },

  /**
   * `trailingSlash: true` was removed along with the static export. It is the
   * right setting for a folder-of-HTML deployment and the wrong one here: it
   * makes Next 308-redirect `/api/chat` to `/api/chat/`, and a redirected POST
   * is a POST whose body some clients silently drop. Nothing else in the app
   * depends on it — routing is by URL hash within a single page.
   */

  /**
   * The chat route reads `public/data/bundle.json` from disk at request time
   * (see `src/lib/bundle.ts`). Next's file tracer follows static `import`s, not
   * runtime `fs.readFileSync(path.join(process.cwd(), …))` calls, so without
   * this the JSON would be present in the CDN's static assets but absent from
   * the serverless function's filesystem — and the route would answer
   * `bundle_unavailable` in production while working perfectly in `next dev`.
   */
  outputFileTracingIncludes: {
    "/api/chat": ["./public/data/*.json"],
  },

  /** Cache-bust every deploy; the bundle is baked into the HTML at build time. */
  generateBuildId: async () => `build-${Date.now()}`,
};

export default nextConfig;
