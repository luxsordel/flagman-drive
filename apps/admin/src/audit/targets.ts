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
};

export function isAuditTargetType(value: string): value is AuditTargetType {
  return Object.hasOwn(labelKeys, value);
}

export function targetLabelKey(type: AuditTargetType): `audit.targets.${TargetLabelKey}` {
  return `audit.targets.${labelKeys[type]}`;
}
