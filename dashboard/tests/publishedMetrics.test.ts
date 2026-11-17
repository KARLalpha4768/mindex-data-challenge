/**
 * The recorded-metric contract.
 *
 * The SQL view and the Analytics headlines used to answer with numbers that
 * are not in the warehouse: S006 rendered as "Crossgates Mall", S008 as a 0%
 * return rate, the top store as S011 at $24,819.30, and every store "within
 * a 5% limit". This file pins the opposite, against the committed bundle:
 * a published statement resolves to that statement's rows, any other
 * statement resolves to nothing, and the headlines are the recorded ones.
 *
 * Run: npm test
 */

import fs from "node:fs";
import path from "node:path";

import {
  headlineFigures,
  listPublishedQueries,
  normaliseSql,
  resolveRecordedSql,
  rowsToCsv,
} from "../src/lib/publishedMetrics";
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

const bundlePath = path.resolve(process.cwd(), "public", "data", "bundle.json");
const bundle = JSON.parse(fs.readFileSync(bundlePath, "utf8")) as Bundle;
const metrics = bundle.analytics.metrics;

const sandboxSrc = fs.readFileSync(
  path.resolve(process.cwd(), "src", "components", "SqlSandbox.tsx"),
  "utf8",
);
const analyticsSrc = fs.readFileSync(
  path.resolve(process.cwd(), "src", "components", "Analytics.tsx"),
  "utf8",
);

console.log("published metrics");

{
  const queries = listPublishedQueries(metrics);
  const ids = queries.map((query) => query.id);
  check(
    "every contract metric is published with its SQL",
    [
      "revenue_reconciliation",
      "return_rate_by_store",
      "top_stores_recent_30d",
      "mom_growth_by_category",
    ].every((id) => ids.includes(id)),
    ids.join(", "),
  );

  const returns = queries.find((query) => query.id === "return_rate_by_store");
  check("return-rate SQL is non-empty", Boolean(returns && returns.sql.length > 40 && returns.rows.length === 15));

  const exact = resolveRecordedSql(returns?.sql ?? "", metrics);
  check("the published return-rate statement resolves to its own rows", exact.status === "recorded");
  if (exact.status === "recorded") {
    const galleria = exact.query.rows.find((row) => row.store_id === "S008");
    check(
      "S008 is Galleria at Crystal Run and breaches 10%, not a 0% compliant row",
      galleria?.store_name === "Galleria at Crystal Run" &&
        galleria.unit_return_rate_pct === 10.48 &&
        galleria.exceeds_threshold === 1,
      JSON.stringify(galleria),
    );
    const lakeside = exact.query.rows.find((row) => row.store_id === "S006");
    check(
      "S006 keeps the recorded store name",
      lakeside?.store_name === "Lakeside Shopping Ctr",
      JSON.stringify(lakeside?.store_name),
    );
    check(
      "the resolved table does not contain the old invented mall name",
      !exact.query.rows.some((row) => String(row.store_name).includes("Crossgates")),
    );
  }

  const wrapped = resolveRecordedSql(`\n  ${returns?.sql ?? ""}\n-- reviewer note\n`, metrics);
  check("whitespace and a trailing comment still identify the same statement", wrapped.status === "recorded");

  const keyword = resolveRecordedSql(
    "SELECT store_id, return_rate_pct, sla_status FROM fact_sales WHERE return_rate_pct > 10",
    metrics,
  );
  check(
    "a statement that only mentions return_rate is not executed",
    keyword.status === "not_executed" && keyword.reason === "unpublished",
    JSON.stringify(keyword),
  );

  const limit = resolveRecordedSql((returns?.sql ?? "").replace(/DESC;?\s*$/i, "DESC LIMIT 3;"), metrics);
  check("changing the published statement withdraws its rows", limit.status === "not_executed");

  check("an empty editor is not executed", resolveRecordedSql("   \n-- only a comment", metrics).status === "not_executed");

  const top = resolveRecordedSql(
    queries.find((query) => query.id === "top_stores_recent_30d")?.sql ?? "",
    metrics,
  );
  if (top.status === "recorded") {
    const first = top.query.rows[0];
    check(
      "the trailing-30-day leader is S008 at 6770.08, not S011 at 24819.30",
      first?.store_id === "S008" && first.net_revenue === 6770.08 && first.transaction_count === 17,
      JSON.stringify(first),
    );
    const csv = rowsToCsv(top.query.columns, top.query.rows);
    check("CSV export starts from the recorded columns and the recorded leader", csv.startsWith("store_id,") && csv.includes("S008") && csv.includes("6770.08"));
    check("CSV does not contain the retired headline figure", !csv.includes("24819.30") && !csv.includes("24,819.30"));
  } else {
    check("top-stores SQL resolves", false, JSON.stringify(top));
  }
}

console.log("\nheadline figures");

{
  const headlines = headlineFigures(metrics);
  check("net revenue is the reconciliation row", headlines.netRevenue === 158044.29, String(headlines.netRevenue));
  check("the preserved discount is 961.48", headlines.discountTotal === 961.48, String(headlines.discountTotal));
  check("the aggregate delta is zero", headlines.aggregateDelta === 0, String(headlines.aggregateDelta));
  check(
    "the headline top store is the first recorded window row",
    headlines.topStore?.storeId === "S008" &&
      headlines.topStore.storeName === "Galleria at Crystal Run" &&
      headlines.topStore.netRevenue === 6770.08,
    JSON.stringify(headlines.topStore),
  );
  check(
    "three stores breach, and S008 is one of them",
    headlines.storesRated === 15 &&
      headlines.breaches.length === 3 &&
      headlines.breaches.map((breach) => breach.storeId).join(",") === "S006,S015,S008",
    JSON.stringify(headlines.breaches.map((breach) => breach.storeId)),
  );
  check(
    "the short month is 2026-06 with a single transacting date, not two days",
    headlines.partialMonth?.yearMonth === "2026-06" && headlines.partialMonth.daysWithData === 1,
    JSON.stringify(headlines.partialMonth),
  );

  const missing = headlineFigures({});
  check(
    "a bundle with no metrics yields nulls rather than a remembered constant",
    missing.netRevenue === null &&
      missing.topStore === null &&
      missing.breaches.length === 0 &&
      missing.partialMonth === null,
  );
}

console.log("\nsource no longer ships the retired figures");

{
  check("the SQL view source has no Crossgates row", !sandboxSrc.includes("Crossgates"));
  check("the SQL view source does not claim a live engine", !sandboxSrc.includes("DuckDB") && !sandboxSrc.includes("Executed in"));
  check("the SQL view source does not invent a millisecond", !sandboxSrc.includes("Math.random"));
  check("the analytics source dropped the $24,819.30 headline", !analyticsSrc.includes("24,819.30") && !analyticsSrc.includes("24819.30"));
  check("the analytics source dropped the 5% all-clear", !analyticsSrc.includes("5.0%") && !analyticsSrc.includes("within 5"));
  check("the analytics source dropped the two-day June claim", !analyticsSrc.includes("2 days") && !analyticsSrc.includes("2-day"));
  check(
    "normalising SQL folds case and drops a comment",
    normaliseSql("SELECT 1\n-- note\n") === "select 1",
  );
}

console.log(`\n${passed} passed, ${failures.length} failed`);
if (failures.length) {
  for (const failure of failures) console.log(`  - ${failure}`);
  process.exitCode = 1;
}
