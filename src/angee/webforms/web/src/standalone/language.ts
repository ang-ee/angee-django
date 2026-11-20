/** What decides the language a form opens in, most specific first. */
export interface LanguageChoice {
  /** `?lang=` of the address, when the visitor or a link asked for one. */
  requested: string | null;
  /** The language the address itself opens the form in; empty leaves it to the visitor. */
  served: string;
  /** The visitor's languages, most wanted first (`navigator.languages`). */
  preferred: readonly string[];
  /** The languages the form is written in. */
  languages: readonly string[];
  defaultLanguage: string;
}

const primary = (tag: string) => tag.toLowerCase().split("-", 1)[0]!;

/**
 * The language a form opens in: the one asked for when the form is written in
 * it, else the address's own, else the first of the visitor's languages the form
 * is written in (the whole tag, then its primary language), else the default.
 */
export function chooseLanguage({ requested, served, preferred, languages, defaultLanguage }: LanguageChoice): string {
  if (requested && languages.includes(requested)) return requested;
  if (served) return served;
  for (const wanted of preferred) {
    const match = languages.find((tag) => tag.toLowerCase() === wanted.toLowerCase())
      ?? languages.find((tag) => primary(tag) === primary(wanted));
    if (match) return match;
  }
  return defaultLanguage;
}

/**
 * SurveyJS's own interface strings (button labels, validation messages): one
 * file per language, which registers itself under the locale code it is keyed by
 * here. English is built in, and a language without a file reads English.
 */
const INTERFACE_STRINGS: Readonly<Record<string, () => Promise<unknown>>> = {
  ar: () => import("survey-core/i18n/arabic"),
  bg: () => import("survey-core/i18n/bulgarian"),
  ca: () => import("survey-core/i18n/catalan"),
  cs: () => import("survey-core/i18n/czech"),
  cy: () => import("survey-core/i18n/welsh"),
  da: () => import("survey-core/i18n/danish"),
  de: () => import("survey-core/i18n/german"),
  el: () => import("survey-core/i18n/greek"),
  es: () => import("survey-core/i18n/spanish"),
  et: () => import("survey-core/i18n/estonian"),
  eu: () => import("survey-core/i18n/basque"),
  fa: () => import("survey-core/i18n/persian"),
  fi: () => import("survey-core/i18n/finnish"),
  fil: () => import("survey-core/i18n/philippines"),
  fr: () => import("survey-core/i18n/french"),
  he: () => import("survey-core/i18n/hebrew"),
  hi: () => import("survey-core/i18n/hindi"),
  hr: () => import("survey-core/i18n/croatian"),
  ht: () => import("survey-core/i18n/haitian-creole"),
  hu: () => import("survey-core/i18n/hungarian"),
  id: () => import("survey-core/i18n/indonesian"),
  is: () => import("survey-core/i18n/icelandic"),
  it: () => import("survey-core/i18n/italian"),
  ja: () => import("survey-core/i18n/japanese"),
  ka: () => import("survey-core/i18n/georgian"),
  kk: () => import("survey-core/i18n/kazakh"),
  ko: () => import("survey-core/i18n/korean"),
  lt: () => import("survey-core/i18n/lithuanian"),
  lv: () => import("survey-core/i18n/latvian"),
  mk: () => import("survey-core/i18n/macedonian"),
  mm: () => import("survey-core/i18n/burmese"),
  ms: () => import("survey-core/i18n/malay"),
  nl: () => import("survey-core/i18n/dutch"),
  no: () => import("survey-core/i18n/norwegian"),
  pl: () => import("survey-core/i18n/polish"),
  pt: () => import("survey-core/i18n/portuguese"),
  "pt-br": () => import("survey-core/i18n/portuguese-br"),
  ro: () => import("survey-core/i18n/romanian"),
  ru: () => import("survey-core/i18n/russian"),
  sk: () => import("survey-core/i18n/slovak"),
  sl: () => import("survey-core/i18n/slovenian"),
  sr: () => import("survey-core/i18n/serbian"),
  sv: () => import("survey-core/i18n/swedish"),
  sw: () => import("survey-core/i18n/swahili"),
  tel: () => import("survey-core/i18n/telugu"),
  tg: () => import("survey-core/i18n/tajik"),
  th: () => import("survey-core/i18n/thai"),
  tr: () => import("survey-core/i18n/turkish"),
  uk: () => import("survey-core/i18n/ukrainian"),
  ur: () => import("survey-core/i18n/urdu"),
  vi: () => import("survey-core/i18n/vietnamese"),
  "zh-cn": () => import("survey-core/i18n/simplified-chinese"),
  "zh-tw": () => import("survey-core/i18n/traditional-chinese"),
};

/** The SurveyJS locale code whose interface strings serve `tag`: its own, else its primary language's. */
export function interfaceLocale(tag: string): string | undefined {
  return [tag.toLowerCase(), primary(tag)].find((code) => Object.hasOwn(INTERFACE_STRINGS, code));
}

/** Load SurveyJS's interface strings for `tag`; resolves to the locale code they registered under, if any. */
export async function loadInterfaceStrings(tag: string): Promise<string | undefined> {
  const code = interfaceLocale(tag);
  if (code) await INTERFACE_STRINGS[code]!();
  return code;
}
