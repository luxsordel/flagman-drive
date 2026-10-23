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

  it('rejects names longer than the limit', () => {
    expect(checkDraft(draft({ lastName: 'a'.repeat(51) }), today)).toEqual({
      ok: false,
      errors: { lastName: 'tooLong' },
    });
  });

  it.each([
    ['1995-03-15', 'dateFormat'],
    ['15/03/1995', 'dateFormat'],
    ['5.3.1995', 'dateFormat'],
    ['31.02.1995', 'dateInvalid'],
    ['00.01.1995', 'dateInvalid'],
    ['15.13.1995', 'dateInvalid'],
    ['15.03.1899', 'dateInvalid'],
    ['24.10.2026', 'dateFuture'],
  ])('rejects the date of birth %s as %s', (birthDate, error) => {
    expect(checkDraft(draft({ birthDate }), today)).toEqual({
      ok: false,
      errors: { birthDate: error },
    });
  });

  it.each(['29.02.2000', '23.10.2026', '01.01.1900', ' 15.03.1995 '])(
    'accepts the date of birth %p',
    (birthDate) => {
      expect(checkDraft(draft({ birthDate }), today).ok).toBe(true);
    },
  );

  it('rejects the last day before the oldest accepted year', () => {
    expect(checkDraft(draft({ birthDate: '31.12.1899' }), today)).toEqual({
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

  it.each(['ivan', 'ivan@', 'ivan@example', 'iv an@example.com'])(
    'rejects the email %s',
    (email) => {
      expect(checkDraft(draft({ email }), today)).toEqual({
        ok: false,
        errors: { email: 'email' },
      });
    },
  );
});
