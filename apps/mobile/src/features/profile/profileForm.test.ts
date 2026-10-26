import { checkDraft, draftFromProfile, formatDateInput, type ProfileDraft } from './profileForm';
import { profile } from './testing';

const today = new Date(2026, 9, 23);

const draft = (overrides: Partial<ProfileDraft> = {}): ProfileDraft => ({
  firstName: 'Ivan',
  lastName: 'Petrov',
  birthDate: '15.03.1995',
  email: '',
  ...overrides,
});

describe('draftFromProfile', () => {
  it('shows the stored date the Belarusian way and blanks for missing values', () => {
    expect(draftFromProfile(profile({ birth_date: '1995-03-15', email: null }))).toEqual({
      firstName: 'Ivan',
      lastName: 'Petrov',
      birthDate: '15.03.1995',
      email: '',
    });
    expect(draftFromProfile(profile({ birth_date: null })).birthDate).toBe('');
  });
});

describe('formatDateInput', () => {
  it.each([
    ['', ''],
    ['1', '1'],
    ['15', '15'],
    ['150', '15.0'],
    ['1503', '15.03'],
    ['15031995', '15.03.1995'],
    ['15/03/1995', '15.03.1995'],
  ])('puts the dots into %p typed from scratch', (typed, shown) => {
    expect(formatDateInput(typed, '')).toBe(shown);
  });

  it('removes the digit before a deleted dot instead of bringing the dot back', () => {
    expect(formatDateInput('1503.1995', '15.03.1995')).toBe('10.31.995');
    expect(formatDateInput('1503', '15.03')).toBe('10.3');
  });

  it('keeps a full date when a ninth digit is typed into it', () => {
    expect(formatDateInput('125.03.1995', '15.03.1995')).toBe('15.03.1995');
  });

  it.each([
    ['1995-03-15', '15.03.1995'],
    ['5.3.1995', '05.03.1995'],
    [' 15/3/1995 ', '15.03.1995'],
  ])('understands the pasted date %p', (pasted, shown) => {
    expect(formatDateInput(pasted, '')).toBe(shown);
  });
});

describe('checkDraft', () => {
  it('turns a valid draft into an update with trimmed values', () => {
    expect(checkDraft(draft({ firstName: '  Ivan ', email: ' ivan@example.com ' }), today)).toEqual(
      {
        ok: true,
        update: {
          first_name: 'Ivan',
          last_name: 'Petrov',
          birth_date: '1995-03-15',
          email: 'ivan@example.com',
        },
      },
    );
  });

  it('sends a blank email as null', () => {
    const result = checkDraft(draft({ email: '   ' }), today);
    expect(result.ok && result.update.email).toBeNull();
  });

  it('requires the names and the date of birth', () => {
    expect(checkDraft(draft({ firstName: ' ', lastName: '', birthDate: '' }), today)).toEqual({
      ok: false,
      errors: { firstName: 'required', lastName: 'required', birthDate: 'required' },
    });
  });

  it('neither checks nor sends the name and birth date once they are locked', () => {
    const locked = { identityLocked: true };
    const empty = draft({
      firstName: '',
      lastName: '',
      birthDate: '',
      email: ' anna@example.com ',
    });

    expect(checkDraft(empty, today, locked)).toEqual({
      ok: true,
      update: { email: 'anna@example.com' },
    });
    expect(checkDraft(draft({ email: 'anna@' }), today, locked)).toEqual({
      ok: false,
      errors: { email: 'email' },
    });
  });

  it('shows names with null from the API as blanks', () => {
    expect(draftFromProfile(profile({ first_name: null, last_name: null }))).toMatchObject({
      firstName: '',
      lastName: '',
    });
  });

  it('accepts names up to the API limit and rejects longer ones', () => {
    expect(checkDraft(draft({ lastName: 'a'.repeat(100) }), today).ok).toBe(true);
    expect(checkDraft(draft({ lastName: 'a'.repeat(101) }), today)).toEqual({
      ok: false,
      errors: { lastName: 'tooLong' },
    });
  });

  it('counts a letter with a combining mark as one character, as the API does', () => {
    const name = 'E\u0308'.repeat(100);
    const result = checkDraft(draft({ firstName: name }), today);
    expect(result.ok && result.update.first_name).toBe('\u00cb'.repeat(100));
  });

  it('turns any run of spaces into one plain space', () => {
    const result = checkDraft(draft({ firstName: 'Anna\u00a0 \u2009Maria' }), today);
    expect(result.ok && result.update.first_name).toBe('Anna Maria');
  });

  it.each(['An\u200bna', 'An\u202ena', 'An\nna', 'An\u2028na', 'An\u0000na'])(
    'rejects a name with an invisible or control character: %j',
    (firstName) => {
      expect(checkDraft(draft({ firstName }), today)).toEqual({
        ok: false,
        errors: { firstName: 'nameCharacters' },
      });
    },
  );

  it.each([
    ['1995-03-15', 'dateFormat'],
    ['15/03/1995', 'dateFormat'],
    ['5.3.1995', 'dateFormat'],
    ['31.02.1995', 'dateInvalid'],
    ['00.01.1995', 'dateInvalid'],
    ['15.13.1995', 'dateInvalid'],
    ['15.03.1899', 'dateInvalid'],
    ['31.12.1905', 'dateInvalid'],
    ['24.10.2026', 'dateFuture'],
  ])('rejects the date of birth %s as %s', (birthDate, error) => {
    expect(checkDraft(draft({ birthDate }), today)).toEqual({
      ok: false,
      errors: { birthDate: error },
    });
  });

  it.each(['29.02.2000', '23.10.2026', '01.01.1906', ' 15.03.1995 '])(
    'accepts the date of birth %p',
    (birthDate) => {
      expect(checkDraft(draft({ birthDate }), today).ok).toBe(true);
    },
  );

  it('accepts dates back to 1 January 120 years ago, as the API does', () => {
    expect(checkDraft(draft({ birthDate: '01.01.1907' }), new Date(2027, 0, 1)).ok).toBe(true);
    expect(checkDraft(draft({ birthDate: '31.12.1906' }), new Date(2027, 0, 1))).toEqual({
      ok: false,
      errors: { birthDate: 'dateInvalid' },
    });
  });

  it('rejects an email longer than the limit', () => {
    const email = `${'a'.repeat(243)}@example.com`;
    expect(email).toHaveLength(255);
    expect(checkDraft(draft({ email }), today)).toEqual({
      ok: false,
      errors: { email: 'tooLong' },
    });
  });

  it.each([
    'ivan',
    'ivan@',
    'ivan@example',
    'iv an@example.com',
    'иван@example.com',
    'ivan@пример.by',
  ])('rejects the email %s', (email) => {
    expect(checkDraft(draft({ email }), today)).toEqual({
      ok: false,
      errors: { email: 'email' },
    });
  });
});
