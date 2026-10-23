import type { Profile, ProfileUpdate } from './profileApi';

/** What the client typed, before it is checked and turned into a profile update. */
export interface ProfileDraft {
  firstName: string;
  lastName: string;
  /** `DD.MM.YYYY`, the way dates are written in Belarus. */
  birthDate: string;
  email: string;
}

export type DraftField = keyof ProfileDraft;

export type FieldError =
  'required' | 'tooLong' | 'dateFormat' | 'dateInvalid' | 'dateFuture' | 'email';

// `undefined` allowed explicitly: the screen clears a field's error by setting it so.
export type DraftErrors = { [K in DraftField]?: FieldError | undefined };

export type DraftCheck = { ok: true; update: ProfileUpdate } | { ok: false; errors: DraftErrors };

// The limits below are the client's proposal for #33; the API has the final word.
// Long enough for any real name, short enough to fit on a rental contract.
export const maxNameLength = 50;
// RFC 5321 limit for a forward path.
export const maxEmailLength = 254;
// Nobody older than this signs up for car sharing; anything earlier is a typo in the year.
const oldestBirthYear = 1900;

export function draftFromProfile(profile: Profile): ProfileDraft {
  return {
    firstName: profile.first_name,
    lastName: profile.last_name,
    birthDate: profile.birth_date === null ? '' : isoToDisplayDate(profile.birth_date),
    email: profile.email ?? '',
  };
}

const dateDigits = 8;

/**
 * Turns what the client typed or pasted into `DD.MM.YYYY`, putting the dots in by itself, so
 * a keypad without punctuation (Android `number-pad`) is enough. `previous` is the value
 * before this change: it tells a deleted dot from a typed character.
 */
export function formatDateInput(text: string, previous: string): string {
  const pasted = parsePastedDate(text.trim());
  if (pasted !== null) return pasted;
  let digits = onlyDigits(text);
  // Backspace on a dot removes the digit before it; otherwise the dot comes straight back.
  if (text.length < previous.length && digits === onlyDigits(previous)) {
    let at = 0;
    while (text[at] === previous[at]) at += 1;
    const before = onlyDigits(previous.slice(0, at)).length;
    if (before > 0) digits = digits.slice(0, before - 1) + digits.slice(before);
  }
  // A ninth digit means a typo in a full date: shifting every digit along would hide it.
  if (digits.length > dateDigits) return previous;
  return [digits.slice(0, 2), digits.slice(2, 4), digits.slice(4)]
    .filter((part) => part !== '')
    .join('.');
}

function onlyDigits(text: string): string {
  return text.replace(/\D/g, '');
}

// A date copied from elsewhere, as ISO or with single-digit day and month.
function parsePastedDate(text: string): string | null {
  const iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (iso !== null) return `${iso[3] ?? ''}.${iso[2] ?? ''}.${iso[1] ?? ''}`;
  const short = /^(\d{1,2})[./](\d{1,2})[./](\d{4})$/.exec(text);
  if (short === null) return null;
  const [, day = '', month = '', year = ''] = short;
  return `${day.padStart(2, '0')}.${month.padStart(2, '0')}.${year}`;
}

/**
 * Checks the draft before sending, so the client sees mistakes without a round trip.
 * The date of birth is required: the operator checks the age against it at KYC review (#35),
 * and the age limit itself is checked there, not here.
 */
export function checkDraft(draft: ProfileDraft, today: Date): DraftCheck {
  const errors: DraftErrors = {};
  const firstName = draft.firstName.trim();
  const lastName = draft.lastName.trim();
  const email = draft.email.trim();

  const nameError = (name: string): FieldError | undefined =>
    name === '' ? 'required' : length(name) > maxNameLength ? 'tooLong' : undefined;
  const firstNameError = nameError(firstName);
  if (firstNameError) errors.firstName = firstNameError;
  const lastNameError = nameError(lastName);
  if (lastNameError) errors.lastName = lastNameError;

  const birthDate = parseBirthDate(draft.birthDate.trim(), today);
  if (!birthDate.ok) errors.birthDate = birthDate.error;

  if (length(email) > maxEmailLength) errors.email = 'tooLong';
  else if (email !== '' && !isEmail(email)) errors.email = 'email';

  if (!birthDate.ok || Object.keys(errors).length > 0) return { ok: false, errors };
  return {
    ok: true,
    update: {
      first_name: firstName,
      last_name: lastName,
      birth_date: birthDate.iso,
      email: email === '' ? null : email,
    },
  };
}

function parseBirthDate(
  text: string,
  today: Date,
): { ok: true; iso: string } | { ok: false; error: FieldError } {
  if (text === '') return { ok: false, error: 'required' };
  const match = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(text);
  if (match === null) return { ok: false, error: 'dateFormat' };
  const [, dd = '', mm = '', yyyy = ''] = match;
  const day = Number(dd);
  const month = Number(mm);
  const year = Number(yyyy);
  // Date.UTC rolls 31.02 over into March, so a round trip tells a real date from a typo.
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    year < oldestBirthYear ||
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  ) {
    return { ok: false, error: 'dateInvalid' };
  }
  const iso = `${yyyy}-${mm}-${dd}`;
  if (iso > localIsoDate(today)) return { ok: false, error: 'dateFuture' };
  return { ok: true, iso };
}

function isoToDisplayDate(iso: string): string {
  const [year, month, day] = iso.split('-');
  return `${day ?? ''}.${month ?? ''}.${year ?? ''}`;
}

// Calendar date in the device's time zone, not UTC: just after midnight in Minsk UTC is
// still yesterday, and a client born today would be told the date is in the future.
function localIsoDate(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${String(date.getFullYear())}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

// Code points, as Python's len() in the API counts them, not UTF-16 units of a JS string.
function length(text: string): number {
  return Array.from(text).length;
}

// Deliberately loose: only the server can tell whether a mailbox exists.
function isEmail(text: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(text);
}
