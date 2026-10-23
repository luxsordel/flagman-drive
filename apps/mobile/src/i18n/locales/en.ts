import type { Translation } from '../types';

export const en: Translation = {
  common: {
    appName: 'FLAGMAN Drive',
  },
  auth: {
    login: {
      title: 'Sign in',
      subtitle: 'Enter your phone number and we will text you a code',
      phoneLabel: 'Phone number',
      phoneHint: 'Belarusian mobile number, 9 digits after +375',
      submit: 'Get code',
    },
    code: {
      title: 'Code from SMS',
      subtitle: 'We sent a code to {{phone}}',
      codeLabel: 'Code',
      submit: 'Sign in',
      resend: 'Send the code again',
      resendIn: 'You can request a new code in {{time}}',
      changePhone: 'Change number',
    },
    errors: {
      otpInvalid: 'Wrong code. Check it and enter it again',
      otpExpired: 'This code no longer works. Request a new one',
      rateLimited: 'Too many attempts. Try again later',
      rateLimitedFor: 'Too many attempts. Try again in {{time}}',
      signInUnavailable: 'Sign-in is temporarily unavailable. Try again later',
      signInNotAllowed: 'The pilot is open to test numbers only for now',
      network: 'Cannot reach the server. Check your connection',
      unknown: 'Something went wrong. Please try again',
    },
    signOut: 'Sign out',
  },
  home: {
    title: 'You are signed in',
    subtitle: 'A map with cars will appear here soon',
  },
  kyc: {
    card: {
      title: 'Documents',
      open: 'Open',
      loading: 'Loading the status…',
    },
    statusShort: {
      not_submitted: 'Not sent',
      pending: 'Under review',
      approved: 'Approved',
      rejected: 'Rejected',
    },
    status: {
      title: 'Document check',
      not_submitted:
        'To rent a car, upload your driving licence, passport and a selfie with the licence',
      pending: 'We are checking your documents. They cannot be changed during the check',
      submittedAt: 'Sent on {{date}}',
      approved: 'Your documents are approved, you can rent a car',
      rejected: 'Your documents did not pass the check. Replace the photos and send them again',
      reason: 'Reason: {{reason}}',
      start: 'Upload documents',
      resubmit: 'Fix and send again',
      refresh: 'Refresh status',
      home: 'Back to home',
    },
    steps: {
      license_front: {
        title: 'Licence: front',
        hint: 'The side with your photo. All corners in the frame, text readable, no glare',
      },
      license_back: {
        title: 'Licence: back',
        hint: 'All corners in the frame, text readable, no glare',
      },
      passport: {
        title: 'Passport',
        hint: 'The page with your photo. All fields readable, no glare',
      },
      selfie_with_license: {
        title: 'Selfie with licence',
        hint: 'Hold the licence next to your face: both your face and the licence photo must be visible',
      },
    },
    upload: {
      step: 'Step {{current}} of {{total}}',
      takePhoto: 'Take a photo',
      fromGallery: 'Choose from gallery',
      chooseFile: 'Choose a file',
      retake: 'Choose another photo',
      send: 'Send photo',
      sending: 'Sending the photo: {{percent}}%',
      retry: 'Try again',
      preview: 'Photo: {{document}}',
      toOverview: 'Back to the document list',
    },
    review: {
      title: 'Your documents',
      subtitle: 'Check that all photos are uploaded and send them for review',
      uploaded: 'Uploaded',
      missing: 'Not uploaded',
      upload: 'Upload',
      replace: 'Replace',
      submit: 'Send for review',
      back: 'Back',
    },
    errors: {
      network: 'Cannot reach the server. Check your connection',
      sessionExpired: 'Your session has ended. Sign out and sign in again',
      tooLarge: 'The photo is too large. Take it again',
      unsupported: 'Could not read the photo. A JPEG, PNG or WebP image is needed',
      locked: 'Your documents are already under review and cannot be changed',
      incomplete: 'Not all documents are uploaded',
      rateLimited: 'Too many attempts. Try again later',
      rateLimitedFor: 'Too many attempts. Try again in {{time}}',
      storageUnavailable: 'Document upload is temporarily unavailable. Try again later',
      cameraDenied:
        'No access to the camera. Allow it in the phone settings or choose a photo from the gallery',
      unknown: 'Something went wrong. Please try again',
    },
  },
};
