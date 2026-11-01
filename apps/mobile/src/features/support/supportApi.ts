import { parseRetryAfter, problemSlug, type components } from '@flagman/api-client';

import type { ApiClient } from '../auth/session/authSession';
import { readPhoto } from '../kyc/readPhoto';
import { sendPhoto, type PhotoBody } from '../kyc/sendPhoto';
import type { LatLon } from '../map/mapConfig';

export type SupportMessage = components['schemas']['MessageOut'];
export type MessagePage = components['schemas']['MessagePageOut'];
export type Incident = components['schemas']['IncidentOut'];
export type IncidentType = components['schemas']['IncidentType'];

export type SupportErrorKind =
  | 'network'
  | 'sessionExpired'
  | 'rateLimited'
  | 'tooLarge'
  | 'unsupported'
  | 'photoLimit'
  | 'tooManyIncidents'
  | 'storageUnavailable'
  | 'unknown';

export interface SupportError {
  kind: SupportErrorKind;
  retryAfter?: number;
}

export type SupportResult<T> = { ok: true; value: T } | { ok: false; error: SupportError };

export interface NewIncident {
  type: IncidentType;
  description: string;
  location: LatLon | null;
  /** The rental of the receipt the client reports from; an active one the server finds itself. */
  rentalId: string | null;
}

export interface SupportApi {
  /** Messages newer than `after`; what comes back no longer counts as unread. */
  messages(after: number): Promise<SupportResult<MessagePage>>;
  /** `clientMessageId` makes a resend safe: the server saves the message once. */
  sendText(
    text: string,
    rentalId: string | null,
    clientMessageId: string,
  ): Promise<SupportResult<SupportMessage>>;
  sendPhoto(photoUri: string, clientMessageId: string): Promise<SupportResult<SupportMessage>>;
  /** A short-lived link; not kept anywhere. */
  photoUrl(messageId: string): Promise<SupportResult<string>>;
  reportIncident(incident: NewIncident): Promise<SupportResult<Incident>>;
  addIncidentPhoto(incidentId: string, photoUri: string): Promise<SupportResult<null>>;
}

export interface SupportApiOptions {
  /** Client that already sends the access token. */
  api: ApiClient;
  baseUrl: string;
  /** Null once signed out; throws when the API cannot be reached (see AuthSession). */
  getAccessToken: () => Promise<string | null>;
  loadPhoto?: (uri: string) => Promise<PhotoBody>;
  createXhr?: () => XMLHttpRequest;
}

const kindByProblem: Readonly<Record<string, SupportErrorKind>> = {
  'rate-limited': 'rateLimited',
  'access-token-invalid': 'sessionExpired',
  'support-photo-too-large': 'tooLarge',
  'support-photo-type-unsupported': 'unsupported',
  'incident-photo-limit': 'photoLimit',
  'too-many-open-incidents': 'tooManyIncidents',
  'photo-storage-unavailable': 'storageUnavailable',
};

export function supportErrorFromResponse(
  body: unknown,
  status: number,
  retryAfterHeader: string | null,
): SupportError {
  const slug = problemSlug(body);
  // A proxy in front of the API answers 413 itself, without Problem Details.
  const kind =
    slug === null ? (status === 413 ? 'tooLarge' : 'unknown') : (kindByProblem[slug] ?? 'unknown');
  if (kind !== 'rateLimited') return { kind };
  const retryAfter = parseRetryAfter(retryAfterHeader);
  return retryAfter === null ? { kind } : { kind, retryAfter };
}

type Sent<T> = Promise<{ data?: T; error?: unknown; response: Response }>;

async function send<T, R>(request: Sent<T>, pick: (data: T) => R): Promise<SupportResult<R>> {
  try {
    const { data, error, response } = await request;
    if (data !== undefined) return { ok: true, value: pick(data) };
    return {
      ok: false,
      error: supportErrorFromResponse(error, response.status, response.headers.get('Retry-After')),
    };
  } catch {
    return { ok: false, error: { kind: 'network' } };
  }
}

export function createSupportApi({
  api,
  baseUrl,
  getAccessToken,
  loadPhoto = readPhoto,
  createXhr,
}: SupportApiOptions): SupportApi {
  const origin = baseUrl.replace(/\/+$/, '');

  async function upload<T>(url: string, photoUri: string): Promise<SupportResult<T>> {
    try {
      const token = await getAccessToken();
      if (token === null) return { ok: false, error: { kind: 'sessionExpired' } };
      const photo = await loadPhoto(photoUri).catch(() => null);
      if (photo === null) return { ok: false, error: { kind: 'unsupported' } };
      const response = await sendPhoto({
        url,
        method: 'POST',
        accessToken: token,
        photo,
        ...(createXhr ? { createXhr } : {}),
      });
      if (response.status >= 200 && response.status < 300) {
        return { ok: true, value: response.body as T };
      }
      return {
        ok: false,
        error: supportErrorFromResponse(response.body, response.status, response.retryAfter),
      };
    } catch {
      return { ok: false, error: { kind: 'network' } };
    }
  }

  return {
    messages: (after) =>
      send(api.GET('/support/messages', { params: { query: { after } } }), (body) => body),
    sendText: (text, rentalId, clientMessageId) =>
      send(
        api.POST('/support/messages', {
          body: {
            text,
            client_message_id: clientMessageId,
            ...(rentalId === null ? {} : { rental_id: rentalId }),
          },
        }),
        (body) => body,
      ),
    sendPhoto: (photoUri, clientMessageId) =>
      upload(
        `${origin}/support/messages/photos?client_message_id=${encodeURIComponent(clientMessageId)}`,
        photoUri,
      ),
    photoUrl: (messageId) =>
      send(
        api.GET('/support/messages/{message_id}/photo-url', {
          params: { path: { message_id: messageId } },
        }),
        (body) => body.url,
      ),
    reportIncident: ({ type, description, location, rentalId }) =>
      send(
        api.POST('/support/incidents', {
          body: {
            type,
            description,
            ...(location === null ? {} : { location }),
            ...(rentalId === null ? {} : { rental_id: rentalId }),
          },
        }),
        (body) => body,
      ),
    async addIncidentPhoto(incidentId, photoUri) {
      const sent = await upload<unknown>(
        `${origin}/support/incidents/${encodeURIComponent(incidentId)}/photos`,
        photoUri,
      );
      return sent.ok ? { ok: true, value: null } : sent;
    },
  };
}
