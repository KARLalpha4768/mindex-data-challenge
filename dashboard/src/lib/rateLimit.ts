/**
 * A fixed-window, in-memory, per-key rate limiter.
 *
 * WHAT THIS PROTECTS AGAINST
 * --------------------------
 * One bored visitor holding down Enter on a public URL, or a naive script that
 * loops without a delay. That is the realistic threat for a portfolio dashboard
 * and this stops it dead, for free, with no external dependency.
 *
 * WHAT THIS DOES **NOT** PROTECT AGAINST — stated plainly, because a security
 * control you have misjudged is worse than one you know is partial:
 *
 *   • It is PER INSTANCE. Vercel runs the route as a serverless function and
 *     will happily run many concurrent instances; each gets its own empty Map.
 *     A distributed or merely well-parallelised caller multiplies the effective
 *     limit by the number of live instances. Only a shared store (Vercel KV,
 *     Upstash, Redis) makes this a real quota guard.
 *   • It is FORGETFUL. Instances are recycled constantly; a cold start resets
 *     every counter. Sustained low-rate abuse across cold starts is invisible.
 *   • The key is an IP address taken from `x-forwarded-for`, which is a header.
 *     On Vercel the leftmost entry is set by the platform and is trustworthy
 *     enough for this purpose, but IPs are shared (NAT, corporate egress) and
 *     rotatable (mobile, VPN). It is a speed bump, not an identity.
 *   • It does nothing about cost per request. That is bounded separately, by
 *     the context-token budget in `grounding.ts` and `maxOutputTokens` in the
 *     route.
 *
 * The genuine backstop is the spend cap on the Google Cloud project that issued
 * the key. This limiter's job is only to make the common accident cheap. That
 * trade-off is deliberate: adding a KV dependency to a review artefact costs a
 * reviewer more (another service to provision) than it saves.
 */

interface Window {
  /** Requests observed in the current window. */
  count: number;
  /** Epoch ms at which the current window closes. */
  resetAt: number;
}

export interface RateLimitVerdict {
  allowed: boolean;
  /** Requests left in this window after accounting for the current one. */
  remaining: number;
  /** Seconds until the window resets. Sent as `Retry-After` when blocked. */
  retryAfterSeconds: number;
  limit: number;
}

export interface RateLimitOptions {
  /** Max requests per key per window. */
  limit: number;
  /** Window length in milliseconds. */
  windowMs: number;
  /**
   * Cap on distinct keys held in memory. Without it, a caller cycling spoofed
   * IPs turns the limiter itself into a slow memory leak — the limiter must not
   * become the outage. On overflow the whole map is cleared, which is crude but
   * fail-open in the direction of availability rather than of unbounded growth.
   */
  maxKeys?: number;
}

const buckets = new Map<string, Window>();

/**
 * Record one request against `key` and say whether it is allowed.
 *
 * Fixed window rather than sliding: a sliding log needs per-request timestamps
 * and eviction logic to bound a burst that this application does not care about
 * distinguishing. The failure mode of a fixed window — up to 2×limit across a
 * window boundary — is irrelevant at these numbers.
 */
export function rateLimit(key: string, options: RateLimitOptions): RateLimitVerdict {
  const { limit, windowMs, maxKeys = 5000 } = options;
  const now = Date.now();

  if (buckets.size > maxKeys) buckets.clear();

  // Opportunistic sweep. No timers: a serverless instance can be frozen between
  // invocations, so an interval is not a reliable place to do this work.
  if (buckets.size > 256) {
    for (const [k, w] of buckets) if (w.resetAt <= now) buckets.delete(k);
  }

  const existing = buckets.get(key);
  if (!existing || existing.resetAt <= now) {
    buckets.set(key, { count: 1, resetAt: now + windowMs });
    return {
      allowed: true,
      remaining: limit - 1,
      retryAfterSeconds: Math.ceil(windowMs / 1000),
      limit,
    };
  }

  existing.count += 1;
  const retryAfterSeconds = Math.max(1, Math.ceil((existing.resetAt - now) / 1000));
  return {
    allowed: existing.count <= limit,
    remaining: Math.max(0, limit - existing.count),
    retryAfterSeconds,
    limit,
  };
}

/** Test seam. Never called by the route. */
export function __resetRateLimiter(): void {
  buckets.clear();
}

/**
 * Best-effort client identity.
 *
 * `x-forwarded-for` is a comma-separated chain, client-first. Vercel prepends
 * the real peer address, so the leftmost entry is the one to use. Anywhere
 * else, treat it as advisory. Falls back to a shared bucket, which is the safe
 * direction to fail: unidentified traffic shares one small allowance rather
 * than each getting its own.
 */
export function clientKeyFrom(headers: Headers): string {
  const forwarded = headers.get("x-forwarded-for");
  if (forwarded) {
    const first = forwarded.split(",")[0]?.trim();
    if (first) return first;
  }
  return headers.get("x-real-ip")?.trim() || "unknown";
}
