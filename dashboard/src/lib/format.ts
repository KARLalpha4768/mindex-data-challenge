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

/** `value` is a ratio (0.1234 -> "12.34%"). */
export function formatRatioPct(value: unknown, digits = 2): string {
  const n = Number(value);
  if (value === null || value === undefined || !Number.isFinite(n)) return EM_DASH;
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
 * Best-effort cell rendering for the heterogeneous analytics tables.
 *
 * Column semantics are inferred from the column NAME because the bundle carries
 * no per-column type metadata. This is a presentation-layer convenience only —
 * it never alters the underlying value.
 */
export function formatMetricCell(column: string, value: unknown): string {
  if (value === null || value === undefined) return EM_DASH;
  if (typeof value === "boolean") return value ? "yes" : "no";

  const name = column.toLowerCase();
  if (typeof value === "number") {
    // Columns ending in _pct already carry percentage values (12.5, not 0.125).
    // Must check this BEFORE the generic "rate" check to avoid double-multiplication.
    if (name.endsWith("_pct")) {
      // Growth/change percentages get a sign prefix; rate percentages do not.
      if (name.includes("growth") || name.includes("change")) return formatSignedPct(value, 2);
      return formatAlreadyPct(value, 2);
    }
    // "rate" without _pct suffix means a 0-1 ratio.
    if (name.includes("rate") || name.includes("pct_of")) return formatRatioPct(value, 2);
    if (name.includes("growth")) return formatSignedPct(value, 2);
    if (
      name.includes("revenue") ||
      name.includes("amount") ||
      name.includes("value") ||
      name.includes("price")
    ) {
      return formatCurrency(value);
    }
    return formatInt(value);
  }
  return String(value);
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
