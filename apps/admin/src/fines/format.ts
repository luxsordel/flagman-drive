/** The name of a fine type in the panel's language; Russian for anything but English. */
export function fineName(fine: { name_ru: string; name_en: string }, language: string): string {
  return language.startsWith('en') ? fine.name_en : fine.name_ru;
}
