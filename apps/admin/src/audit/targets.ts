import type { components } from '@flagman/api-client';

import type ru from '../i18n/ru.json';

export type AuditTargetType = components['schemas']['AuditTargetType'];

type TargetLabelKey = keyof typeof ru.audit.targets;

// A Record, so a type added to the API fails the type check here until it gets a label.
const labelKeys: Record<AuditTargetType, TargetLabelKey> = {
  staff_member: 'staffMember',
  kyc_application: 'kycApplication',
  zone: 'zone',
  tariff: 'tariff',
  fine_type: 'fineType',
  fine: 'fine',
  car: 'car',
  payment_operation: 'paymentOperation',
  maintenance_task: 'maintenanceTask',
  support_conversation: 'supportConversation',
  incident: 'incident',
  rental: 'rental',
  client: 'client',
};

export function isAuditTargetType(value: string): value is AuditTargetType {
  return Object.hasOwn(labelKeys, value);
}

export function targetLabelKey(type: AuditTargetType): `audit.targets.${TargetLabelKey}` {
  return `audit.targets.${labelKeys[type]}`;
}
