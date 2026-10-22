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
};
