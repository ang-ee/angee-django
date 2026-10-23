let humanLocale = "en";

/** Set the default language for pure human-readable formatters from app i18n.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function setHumanDateLocale(language: string): void {
  humanLocale = language || "en";
}

/** Return the app language shared by human-readable date and number formats. */
export function getHumanLocale(): string {
  return humanLocale;
}
