import Dashboard from "@/components/Dashboard";
import { buildDefectViews, discountImpact, loadBundle } from "@/lib/bundle";

/**
 * The only route.
 *
 * A Server Component, so everything below happens once during `next build`:
 *   - read the JSON bundle from disk
 *   - join catalog x audit x code_index into the row shape the UI renders
 *   - hand the result to the client Dashboard as props
 *
 * The browser therefore receives fully-formed data inside the pre-rendered
 * HTML. No fetch, no loading spinner, no waterfall — which is the whole point
 * of shipping this as a static export.
 *
 * `force-static` is redundant under `output: "export"` but states the intent
 * explicitly for anyone reading this file alone.
 */
export const dynamic = "force-static";

export default function Page() {
  const { bundle, sourceFile, isMock } = loadBundle();
  const defects = buildDefectViews(bundle);
  const discount = discountImpact(bundle);

  return (
    <Dashboard
      bundle={bundle}
      defects={defects}
      discountImpact={discount}
      sourceFile={sourceFile}
      isMock={isMock}
    />
  );
}
