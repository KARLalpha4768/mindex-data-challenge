import fs from "node:fs";
import path from "node:path";

import Dashboard from "@/components/Dashboard";
import { buildDefectViews, discountImpact, loadBundle } from "@/lib/bundle";
import { parseTestInventory, type TestInventory } from "@/lib/reviewerEvidence";

function loadTestInventory(): TestInventory | null {
  const file = path.join(process.cwd(), "public", "data", "test_inventory.json");
  if (!fs.existsSync(file)) return null;
  return parseTestInventory(JSON.parse(fs.readFileSync(file, "utf8")));
}

/**
 * The only route.
 *
 * A Server Component, so everything below happens once during `next build`:
 *   - read the JSON bundle from disk
 *   - join catalog x audit x code_index into the row shape the UI renders
 *   - hand the result to the client Dashboard as props
 *
 * The browser therefore receives fully-formed data inside the pre-rendered
 * HTML. No fetch, no loading spinner, no waterfall.
 *
 * `force-static` used to be redundant under `output: "export"`. It is now
 * load-bearing: the app has a server (it needs one for `/api/chat`, which holds
 * an API key), and this line is what keeps the *page* a build-time artefact
 * rather than something re-rendered per request. The only dynamic surface in
 * the deployment is that one API route.
 */
export const dynamic = "force-static";

export default function Page() {
  const { bundle, sourceFile, isMock } = loadBundle();
  const defects = buildDefectViews(bundle);
  const discount = discountImpact(bundle);
  const testInventory = loadTestInventory();

  return (
    <Dashboard
      bundle={bundle}
      defects={defects}
      discountImpact={discount}
      sourceFile={sourceFile}
      isMock={isMock}
      testInventory={testInventory}
    />
  );
}
