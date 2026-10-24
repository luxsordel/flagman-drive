import type ru from '../i18n/ru.json';
import type { AuditAction } from './api';

type ActionLabelKey = keyof typeof ru.audit.actions;

// A Record, so an action added to the API fails the type check here until it gets a label.
const labelKeys: Record<AuditAction, ActionLabelKey> = {
  'staff.created': 'staffCreated',
  'staff.signed_in': 'staffSignedIn',
  'staff.sign_in_failed': 'staffSignInFailed',
  'staff.signed_out': 'staffSignedOut',
  'staff.totp_enrollment_started': 'staffTotpEnrollmentStarted',
  'staff.totp_enrolled': 'staffTotpEnrolled',
  'staff.totp_reset': 'staffTotpReset',
  'staff.password_changed': 'staffPasswordChanged',
  'staff.disabled': 'staffDisabled',
  'kyc.approved': 'kycApproved',
  'kyc.rejected': 'kycRejected',
  'kyc.document_viewed': 'kycDocumentViewed',
};

export const auditActions = Object.keys(labelKeys) as AuditAction[];

export function isAuditAction(value: string): value is AuditAction {
  return Object.hasOwn(labelKeys, value);
}

export function actionLabelKey(action: AuditAction): `audit.actions.${ActionLabelKey}` {
  return `audit.actions.${labelKeys[action]}`;
}
