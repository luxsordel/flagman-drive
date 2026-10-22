import type { ru } from './locales/ru';

type Strings<T> = { readonly [K in keyof T]: T[K] extends string ? string : Strings<T[K]> };

/** Shape of every locale: Russian is the reference, so a missing English key fails typecheck. */
export type Translation = Strings<typeof ru>;
