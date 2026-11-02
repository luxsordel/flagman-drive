import { fakeApi, json, problem } from '../auth/session/testing';
import { createSupportApi, supportErrorFromResponse } from './supportApi';

function setup() {
  const http = fakeApi();
  return {
    http,
    support: createSupportApi({
      api: http.client,
      baseUrl: 'https://api.test/',
      getAccessToken: () => Promise.resolve('token'),
    }),
  };
}

describe('createSupportApi', () => {
  it('sends a text with its id and the rental', async () => {
    const { http, support } = setup();
    http.on('/support/messages', json({ id: 'm-1' }, 201));

    const sent = await support.sendText('Hello', 'r-1', 'c-1');

    expect(sent.ok).toBe(true);
    expect(http.calls).toEqual([
      {
        path: '/support/messages',
        body: { text: 'Hello', client_message_id: 'c-1', rental_id: 'r-1' },
      },
    ]);
  });

  it('leaves out what the report does not have', async () => {
    const { http, support } = setup();
    http.on('/support/incidents', json({ id: 'i-1' }, 201));

    await support.reportIncident({
      type: 'other',
      description: 'Strange noise',
      location: null,
      rentalId: null,
      clientReportId: 'report-1',
    });

    expect(http.calls).toEqual([
      {
        path: '/support/incidents',
        body: { type: 'other', description: 'Strange noise', client_report_id: 'report-1' },
      },
    ]);
  });

  it('explains too many open reports', async () => {
    const { http, support } = setup();
    http.on('/support/incidents', problem('too-many-open-incidents', 409));

    await expect(
      support.reportIncident({
        type: 'other',
        description: 'Strange noise',
        location: null,
        rentalId: null,
        clientReportId: 'report-1',
      }),
    ).resolves.toEqual({ ok: false, error: { kind: 'tooManyIncidents' } });
  });
});

describe('supportErrorFromResponse', () => {
  it('passes the wait of a rate limit on', () => {
    const body = { type: 'urn:flagman:problem:rate-limited' };

    expect(supportErrorFromResponse(body, 429, '60').kind).toBe('rateLimited');
    expect(supportErrorFromResponse(body, 429, '60').retryAfter).toBe(60);
  });

  it('tells a refusal from a failure worth repeating', () => {
    const body = { type: 'urn:flagman:problem:rental-not-found' };

    expect(supportErrorFromResponse(body, 404, null).kind).toBe('rejected');
    expect(supportErrorFromResponse(null, 422, null).kind).toBe('rejected');
    expect(supportErrorFromResponse(null, 429, null).kind).toBe('unknown');
    expect(supportErrorFromResponse(null, 503, null).kind).toBe('unknown');
  });

  it('takes a 413 of a proxy for a large photo', () => {
    expect(supportErrorFromResponse(null, 413, null)).toEqual({ kind: 'tooLarge' });
  });
});
