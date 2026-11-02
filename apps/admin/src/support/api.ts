import type { components } from '@flagman/api-client';

import { unwrap, type AdminApiClient } from '../api/client';

export type Conversation = components['schemas']['ConversationOut'];
export type StaffMessage = components['schemas']['StaffMessageOut'];
export type StaffMessagePage = components['schemas']['StaffMessagePageOut'];
export type SupportUnread = components['schemas']['UnreadOut'];
export type PhotoUrl = components['schemas']['PhotoUrlOut'];
export type Incident = components['schemas']['StaffIncidentOut'];
export type IncidentPage = components['schemas']['StaffIncidentListOut'];
export type IncidentStatus = components['schemas']['IncidentStatus'];
export type IncidentType = components['schemas']['IncidentType'];
export type RentalStatus = components['schemas']['RentalStatus'];

export const incidentStatuses: readonly IncidentStatus[] = [
  'new',
  'in_progress',
  'resolved',
  'rejected',
];
export const incidentTypes: readonly IncidentType[] = [
  'accident',
  'damage',
  'malfunction',
  'other',
];

/** QUEUE_SIZE_MAX of the API (support/domain.py): a larger value is refused with 422. */
export const supportQueueSize = 100;
/** MESSAGE_TEXT_MAX_LENGTH and INCIDENT_COMMENT_MAX_LENGTH of the API (support/domain.py). */
export const answerMaxLength = 2000;
export const incidentCommentMaxLength = 1000;

/** What the operators still have to deal with: the API sorts only this as a queue. */
const openIncidentStatuses: IncidentStatus[] = ['new', 'in_progress'];

function incidentStatusQuery(status: IncidentFilter['status']) {
  if (status === 'all') return {};
  return { status: status ? [status] : openIncidentStatuses };
}

export interface IncidentFilter {
  /** Nothing means the open ones. */
  status?: IncidentStatus | 'all';
  type?: IncidentType;
}

/** Every call rejects with ApiError when the API refuses, with NetworkError when it is unreachable. */
export interface SupportApi {
  unread(): Promise<SupportUnread>;
  queue(): Promise<Conversation[]>;
  conversation(id: string): Promise<Conversation>;
  /** Messages newer than `after`; what comes back no longer counts as unread. */
  messages(id: string, after: number): Promise<StaffMessagePage>;
  take(id: string): Promise<Conversation>;
  /** `seenSeq` is the last message the operator was shown: anything newer stays unread. */
  reply(id: string, text: string, seenSeq: number): Promise<StaffMessage>;
  close(id: string): Promise<Conversation>;
  /** A short-lived link; the API writes every one to the audit log. */
  messagePhotoUrl(conversationId: string, messageId: string): Promise<PhotoUrl>;
  incidents(filter: IncidentFilter, cursor?: string): Promise<IncidentPage>;
  incident(id: string): Promise<Incident>;
  takeIncident(id: string): Promise<Incident>;
  resolveIncident(id: string, comment: string): Promise<Incident>;
  rejectIncident(id: string, comment: string): Promise<Incident>;
  sendToService(id: string): Promise<Incident>;
  incidentPhotoUrl(incidentId: string, photoId: string): Promise<PhotoUrl>;
}

export function createSupportApi(client: AdminApiClient): SupportApi {
  const conversationPath = (id: string) => ({ params: { path: { conversation_id: id } } });
  const incidentPath = (id: string) => ({ params: { path: { incident_id: id } } });
  return {
    async unread() {
      return unwrap(await client.GET('/admin/support/unread'));
    },
    async queue() {
      const { items } = unwrap(
        await client.GET('/admin/support/conversations', {
          params: { query: { limit: supportQueueSize } },
        }),
      );
      return items;
    },
    async conversation(id) {
      return unwrap(
        await client.GET('/admin/support/conversations/{conversation_id}', conversationPath(id)),
      );
    },
    async messages(id, after) {
      return unwrap(
        await client.GET('/admin/support/conversations/{conversation_id}/messages', {
          params: { path: { conversation_id: id }, query: { after } },
        }),
      );
    },
    async take(id) {
      return unwrap(
        await client.POST(
          '/admin/support/conversations/{conversation_id}/take',
          conversationPath(id),
        ),
      );
    },
    async reply(id, text, seenSeq) {
      return unwrap(
        await client.POST('/admin/support/conversations/{conversation_id}/messages', {
          ...conversationPath(id),
          body: { text, seen_seq: seenSeq },
        }),
      );
    },
    async close(id) {
      return unwrap(
        await client.POST(
          '/admin/support/conversations/{conversation_id}/close',
          conversationPath(id),
        ),
      );
    },
    async messagePhotoUrl(conversationId, messageId) {
      return unwrap(
        await client.GET(
          '/admin/support/conversations/{conversation_id}/messages/{message_id}/photo-url',
          { params: { path: { conversation_id: conversationId, message_id: messageId } } },
        ),
      );
    },
    async incidents(filter, cursor) {
      return unwrap(
        await client.GET('/admin/support/incidents', {
          params: {
            query: {
              ...incidentStatusQuery(filter.status),
              ...(filter.type ? { type: filter.type } : {}),
              ...(cursor ? { cursor } : {}),
            },
          },
        }),
      );
    },
    async incident(id) {
      return unwrap(await client.GET('/admin/support/incidents/{incident_id}', incidentPath(id)));
    },
    async takeIncident(id) {
      return unwrap(
        await client.POST('/admin/support/incidents/{incident_id}/take', incidentPath(id)),
      );
    },
    async resolveIncident(id, comment) {
      return unwrap(
        await client.POST('/admin/support/incidents/{incident_id}/resolve', {
          ...incidentPath(id),
          body: { comment },
        }),
      );
    },
    async rejectIncident(id, comment) {
      return unwrap(
        await client.POST('/admin/support/incidents/{incident_id}/reject', {
          ...incidentPath(id),
          body: { comment },
        }),
      );
    },
    async sendToService(id) {
      return unwrap(
        await client.POST(
          '/admin/support/incidents/{incident_id}/send-to-service',
          incidentPath(id),
        ),
      );
    },
    async incidentPhotoUrl(incidentId, photoId) {
      return unwrap(
        await client.GET('/admin/support/incidents/{incident_id}/photos/{photo_id}/url', {
          params: { path: { incident_id: incidentId, photo_id: photoId } },
        }),
      );
    },
  };
}
