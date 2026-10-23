const problemPrefix = 'urn:flagman:problem:';

/**
 * The slug of a Problem Details body (ADR-0006), e.g. `kyc-locked`; null for anything else.
 * A proxy or an outage can answer with HTML or an empty body instead of Problem Details.
 */
export function problemSlug(body: unknown): string | null {
  if (typeof body !== 'object' || body === null || !('type' in body)) return null;
  const type = body.type;
  return typeof type === 'string' && type.startsWith(problemPrefix)
    ? type.slice(problemPrefix.length)
    : null;
}

/** The API sends delta-seconds; an HTTP date or garbage is ignored rather than guessed at. */
export function parseRetryAfter(value: string | null): number | null {
  const trimmed = value?.trim() ?? '';
  return /^\d+$/.test(trimmed) ? Number(trimmed) : null;
}
