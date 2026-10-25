import { profile } from '../profile/testing';
import { canEditDocuments, firstStep, hasProfileForKyc, isUploaded, stepAfter } from './kycFlow';
import { kycApplication } from './testing';

describe('kycFlow', () => {
  it('lets the client change documents only before review or after a rejection', () => {
    expect(canEditDocuments('not_submitted')).toBe(true);
    expect(canEditDocuments('rejected')).toBe(true);
    expect(canEditDocuments('pending')).toBe(false);
    expect(canEditDocuments('approved')).toBe(false);
  });

  it('needs the name and birth date the documents are checked against', () => {
    expect(hasProfileForKyc(profile())).toBe(true);
    expect(hasProfileForKyc(profile({ email: null }))).toBe(true);
    expect(hasProfileForKyc(profile({ first_name: null }))).toBe(false);
    expect(hasProfileForKyc(profile({ last_name: '' }))).toBe(false);
    expect(hasProfileForKyc(profile({ birth_date: null }))).toBe(false);
  });

  it('starts at the first missing document in the step order', () => {
    expect(firstStep(kycApplication())).toBe('license_front');
    expect(firstStep(kycApplication(['license_front', 'passport']))).toBe('license_back');
  });

  it('opens the overview when everything is uploaded', () => {
    const application = kycApplication([
      'license_front',
      'license_back',
      'passport',
      'selfie_with_license',
    ]);
    expect(firstStep(application)).toBe('review');
    expect(isUploaded(application, 'passport')).toBe(true);
  });

  it('goes on to the next missing document, then back to one skipped earlier', () => {
    expect(stepAfter(kycApplication(['license_front']), 'license_front')).toBe('license_back');
    expect(stepAfter(kycApplication(['license_back', 'passport']), 'passport')).toBe(
      'selfie_with_license',
    );
    expect(
      stepAfter(
        kycApplication(['license_back', 'passport', 'selfie_with_license']),
        'selfie_with_license',
      ),
    ).toBe('license_front');
  });

  it('returns to the overview after replacing a photo when nothing is missing', () => {
    const application = kycApplication(
      ['license_front', 'license_back', 'passport', 'selfie_with_license'],
      { status: 'rejected', rejection_reason: 'The selfie is blurry' },
    );
    expect(stepAfter(application, 'license_front')).toBe('review');
  });
});
