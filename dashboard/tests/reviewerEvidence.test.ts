/**
 * The export dialog and the validation page may only hand a reviewer
 * something the bundle or a pytest collection actually contains.
 *
 * Run: npm test
 */

import fs from "node:fs";
import path from "node:path";

import { briefingMarkdown, metricToCsv, modulesOf, parseTestInventory } from "../src/lib/reviewerEvidence";
import type { Bundle } from "../src/lib/types";

let passed = 0;
const failures: string[] = [];

function check(name: string, condition: boolean, detail = ""): void {
  if (condition) {
    passed += 1;
    console.log(`  ok   ${name}`);
  } else {
    failures.push(`${name}${detail ? ` — ${detail}` : ""}`);
    console.log(`  FAIL ${name}${detail ? ` — ${detail}` : ""}`);
  }
}

const root = process.cwd();
const bundle = JSON.parse(
  fs.readFileSync(path.join(root, "public", "data", "bundle.json"), "utf8"),
) as Bundle;
const inventoryRaw = JSON.parse(
  fs.readFileSync(path.join(root, "public", "data", "test_inventory.json"), "utf8"),
);
const exportSrc = fs.readFileSync(path.join(root, "src", "components", "ExportCenterModal.tsx"), "utf8");
const testsSrc = fs.readFileSync(path.join(root, "src", "components", "TestResults.tsx"), "utf8");

console.log("exports");

{
  const briefing = briefingMarkdown(bundle);
  check("briefing quotes the recorded net revenue", briefing.includes("158044.29"), briefing);
  check("briefing quotes the recorded discount", briefing.includes("961.48"));
  check("briefing quotes the recorded fact count", briefing.includes("474"));
  check(
    "briefing does not invent a pass count or a store",
    !briefing.includes("46/46") &&
      !briefing.includes("27 / 27") &&
      !briefing.includes("Crossgates") &&
      !briefing.includes("Destiny USA") &&
      !briefing.includes("PASS"),
  );

  const csv = metricToCsv(bundle.analytics.metrics.return_rate_by_store);
  check("return-rate CSV is the recorded rows", Boolean(csv && csv.includes("Lakeside Shopping Ctr") && csv.includes("10.48")));
  check("return-rate CSV has no invented mall", Boolean(csv && !csv.includes("Crossgates")));
  check("a metric with no rows does not invent a CSV", metricToCsv({ rows: [] }) === null);

  const empty = briefingMarkdown({
    ...bundle,
    analytics: { metrics: {} },
    run: { ...bundle.run, row_counts: { raw: {} } },
  });
  check(
    "a bundle without reconciliation does not keep the old dollar figure",
    !empty.includes("158044.29") && !empty.includes("961.48"),
  );

  check("export source no longer names Crossgates", !exportSrc.includes("Crossgates"));
  check("export source no longer offers a production-ready badge", !exportSrc.includes("Production Ready"));
  check("export source no longer cites the invented transaction id", !exportSrc.includes("T0042"));
}

console.log("\ninventory");

{
  const inventory = parseTestInventory(inventoryRaw);
  check("the committed inventory parses", inventory !== null);
  if (inventory) {
    check("collection count matches the list", inventory.collected === inventory.tests.length && inventory.collected > 80);
    check(
      "node ids are unique and look like pytest ids",
      new Set(inventory.tests.map((test) => test.nodeid)).size === inventory.tests.length &&
        inventory.tests.every((test) => test.nodeid.startsWith("tests/") && test.nodeid.includes("::test")),
    );
    const modules = modulesOf(inventory);
    check(
      "the real cleaning module is present and the fictional one is not",
      modules.some((module) => module.file === "tests/test_cleaning.py") &&
        modules.every((module) => module.file !== "tests/test_defects.py"),
    );
    check(
      "the inventory test itself was collected",
      inventory.tests.some((test) => test.nodeid.includes("test_dashboard_inventory")),
    );
  }

  const claimed = parseTestInventory({
    command: "python -m pytest --collect-only --disable-warnings",
    cwd: "solution",
    collected: 1,
    tests: [{ nodeid: "tests/test_cleaning.py::test_x", doc: null, status: "PASS" }],
  });
  check("an inventory that carries a pass status is rejected", claimed === null);

  const short = parseTestInventory({
    command: "python -m pytest --collect-only --disable-warnings",
    cwd: "solution",
    collected: 2,
    tests: [{ nodeid: "tests/test_cleaning.py::test_x", doc: null }],
  });
  check("a count that disagrees with the list is rejected", short === null);

  check("the validation page no longer prints 27 / 27", !testsSrc.includes("27 / 27") && !testsSrc.includes("27/27"));
  check("the validation page no longer cites test_defects.py", !testsSrc.includes("test_defects.py"));
  check("the validation page no longer badges a row PASS", !testsSrc.includes("PASS"));
}

console.log(`\n${passed} passed, ${failures.length} failed`);
if (failures.length) {
  for (const failure of failures) console.log(`  - ${failure}`);
  process.exitCode = 1;
}
