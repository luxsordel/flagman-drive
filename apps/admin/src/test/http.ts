import type { Mock } from 'vitest';

/** A JSON response as the API sends it. */
export const json = (
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });

/** A Problem Details error of the API (ADR-0006); `type` without the `urn:flagman:problem:` prefix. */
export const problem = (status: number, type: string, headers: Record<string, string> = {}) =>
  json(
    status,
    { type: `urn:flagman:problem:${type}`, title: type, status },
    { 'Content-Type': 'application/problem+json', ...headers },
  );

/** The request a mocked fetch received in its `call`-th call. */
export function sentRequest(
  fetch: Mock<(request: Request) => Promise<Response>>,
  call = 0,
): Request {
  const request = fetch.mock.calls[call]?.[0];
  if (!request) throw new Error('no request was sent');
  return request;
}
