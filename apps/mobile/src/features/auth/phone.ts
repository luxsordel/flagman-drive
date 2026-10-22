export const belarusCountryCode = '+375';

// Mobile operator codes: A1 (29, 44), MTS (29, 33), life:) (25). Landlines cannot receive SMS.
const mobileOperatorCodes = new Set(['25', '29', '33', '44']);

/**
 * Normalises what the user typed after the fixed "+375" prefix to E.164 (`+375XXXXXXXXX`),
 * or returns null. Also accepts a pasted full number (`+375 29 …`, `8 029 …`).
 * Only for form feedback: the server validates the number again.
 */
export function parseBelarusMobile(input: string): string | null {
  let digits = input.replace(/\D/g, '');
  if (digits.length === 12 && digits.startsWith('375')) digits = digits.slice(3);
  else if (digits.length === 11 && digits.startsWith('80')) digits = digits.slice(2);
  if (digits.length !== 9 || !mobileOperatorCodes.has(digits.slice(0, 2))) return null;
  return `${belarusCountryCode}${digits}`;
}
