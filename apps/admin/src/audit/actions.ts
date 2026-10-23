import type ru from '../i18n/ru.json';
import type { AuditAction } from './api';

type ActionLabelKey = keyof typeof ru.audit.actions;

// A Record, so an action added to the API fails the type check here until it gets a label.
const labelKeys: Record<AuditAction, ActionLabelKey> = {
  'staff.signed_in': 'staffSignedIn',
  'staff.sign_in_failed': 'staffSignInFailed',
};

export const auditActions = Object.keys(labelKeys) as AuditAction[];

export function isAuditAction(value: string): value is AuditAction {
  return Object.hasOwn(labelKeys, value);
}

export function actionLabelKey(action: AuditAction): `audit.actions.${ActionLabelKey}` {
  return `audit.actions.${labelKeys[action]}`;
}
