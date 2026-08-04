/**
 * Presentation helpers.
 *
 * One rule underpins all of these: never silently turn "unknown" into a number.
 * `null` and `undefined` render as an em-dash, not as 0 — an analytics table
 * that shows 0% where the denominator was NULL is exactly the class of lie this
 * project is about catching.
 */

const EM_DASH = "—";

const currencyFmt = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

const compactCurrencyFmt = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

const intFmt = new Intl.NumberFormat("en-US");

export function formatCurrency(value: unknown): string {
  const n = Number(value);
  if (value === null || value === undefined || !Number.isFinite(n)) return EM_DASH;
  return currencyFmt.format(n);
}

export function formatCompactCurrency(value: unknown): string {
  const n = Number(value);
  if (value === null || value === undefined || !Number.isFinite(n)) return EM_DASH;
  return compactCurrencyFmt.format(n);
}

export function formatInt(value: unknown): string {
  const n = Number(value);
  if (value === null || value === undefined || !Number.isFinite(n)) return EM_DASH;
  return intFmt.format(n);
}

/** `value` is a ratio (0.1234 -> "12.34%"). If value > 1 or < -1 (e.g. 12.34), it is already percentage-scaled. */
export function formatRatioPct(value: unknown, digits = 2): string {
  const n = Number(value);
  if (value === null || value === undefined || !Number.isFinite(n)) return EM_DASH;
  if (Math.abs(n) > 1.0) return `${n.toFixed(digits)}%`;
  return `${(n * 100).toFixed(digits)}%`;
}

/** `value` is already a percentage (12.34 -> "12.34%"). Unsigned. */
export function formatAlreadyPct(value: unknown, digits = 2): string {
  const n = Number(value);
  if (value === null || value === undefined || !Number.isFinite(n)) return EM_DASH;
  return `${n.toFixed(digits)}%`;
}

/** `value` is already a percentage (12.34 -> "+12.34%"). Signed. */
export function formatSignedPct(value: unknown, digits = 2): string {
  const n = Number(value);
  if (value === null || value === undefined || !Number.isFinite(n)) return EM_DASH;
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

/** ISO timestamp -> "2026-06-02 09:41 UTC". Fixed format, no locale drift. */
export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return EM_DASH;
  const isoStr = String(iso);
  const utcIso = /[Z+-]\d{2}:?\d{2}$|Z$/i.test(isoStr) ? isoStr : `${isoStr}Z`;
  const d = new Date(utcIso);
  if (Number.isNaN(d.getTime())) return isoStr;
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`
  );
}

/**
 * The unit vocabulary declared by `src/analytics/queries.py`. Kept as a string
 * union rather than an enum so it survives JSON round-tripping unchanged.
 */
export type ColumnUnit =
  | "percent" // already scaled 0-100: 12.5 -> "12.50%"
  | "ratio" //   unscaled 0-1:       0.125 -> "12.50%"
  | "currency"
  | "integer"
  | "flag"
  | "text";

/**
 * Cell rendering for the analytics tables.
 *
 * SCALE IS NEVER INFERRED FROM MAGNITUDE. The previous implementation decided
 * that `Math.abs(value) > 1` meant "already a percentage", which is wrong in
 * both directions: a real +150% month-over-month growth arrives as 1.5 in ratio
 * form and would have rendered "1.50%", while a real 0.4% return rate in
 * percentage form would have rendered "40.00%". It also silently produced
 * "1250.00%" for a correct 12.5 whenever the caller had already scaled.
 *
 * The producer declares the unit. `metric.column_units[column]` comes straight
 * from the SQL author in `queries.py`. When a unit is declared we obey it and
 * do no arithmetic beyond what the unit says.
 *
 * The name-based fallback below applies only to bundles produced before units
 * existed. It is deliberately conservative and follows one stated convention —
 * `_pct` means 0-100, `_ratio`/`_rate` means 0-1 — so it is predictable rather
 * than clever. If neither matches, the number is rendered as-is.
 */
export function formatMetricCell(
  column: string,
  value: unknown,
  unit?: ColumnUnit,
): string {
  if (value === null || value === undefined) return EM_DASH;
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value !== "number") return String(value);

  const name = column.toLowerCase();
  // Growth and change columns carry a sign that is the whole point of the
  // number, so they are rendered signed. This is a display choice, not a
  // scaling one, and is independent of the unit.
  const signed = name.includes("growth") || name.includes("change");

  // ── Declared unit: the trusted path ──────────────────────────────────────
  if (unit) {
    switch (unit) {
      case "percent":
        return signed ? formatSignedPct(value, 2) : formatAlreadyPct(value, 2);
      case "ratio":
        return signed
          ? formatSignedPct(value * 100, 2)
          : formatAlreadyPct(value * 100, 2);
      case "currency":
        return formatCurrency(value);
      case "integer":
        return formatInt(value);
      case "flag":
        return value ? "yes" : "no";
      case "text":
        return String(value);
    }
  }

  // ── Fallback for unit-less legacy bundles ────────────────────────────────
  // WHY suffix-based and not magnitude-based: a suffix is a decision someone
  // made; a magnitude is an accident of the data on a given day.
  if (name.endsWith("_pct")) {
    return signed ? formatSignedPct(value, 2) : formatAlreadyPct(value, 2);
  }
  if (name.endsWith("_ratio") || name.endsWith("_rate") || name.includes("rate_")) {
    return signed
      ? formatSignedPct(value * 100, 2)
      : formatAlreadyPct(value * 100, 2);
  }
  if (
    name.includes("revenue") ||
    name.includes("amount") ||
    name.includes("value") ||
    name.includes("price") ||
    name.includes("spend")
  ) {
    return formatCurrency(value);
  }
  return formatInt(value);
}

/** Column key -> human header. "net_revenue" -> "Net revenue". */
export function humaniseColumn(key: string): string {
  const spaced = key.replace(/_/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Right-align numerics; left-align identifiers and prose. */
export function isNumericColumn(rows: Record<string, unknown>[], key: string): boolean {
  return rows.some((r) => typeof r[key] === "number");
}

export { EM_DASH };
