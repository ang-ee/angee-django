import { surveyLocalization } from "survey-core";

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
const same = (left: string, right: string) => left.toLowerCase() === right.toLowerCase();

/**
 * The language a form opens in: the one asked for when the form is written in
 * it, else the address's own, else the first of the visitor's languages the form
 * is written in (the whole tag, then its primary language), else the default.
 * Tags compare without regard to case, and the answer is the form's own spelling.
 */
export function chooseLanguage({ requested, served, preferred, languages, defaultLanguage }: LanguageChoice): string {
  const asked = requested ? languages.find((tag) => same(tag, requested)) : undefined;
  if (asked) return asked;
  if (served) return served;
  for (const wanted of preferred) {
    const match = languages.find((tag) => same(tag, wanted)) ?? languages.find((tag) => primary(tag) === primary(wanted));
    if (match) return match;
  }
  return defaultLanguage;
}

/** Tags that name a language another tag already serves here. */
const SAME_LANGUAGE: Readonly<Record<string, string>> = {
  no: "nb",
  tl: "fil",
  "zh-hk": "zh-hant",
  "zh-mo": "zh-hant",
  "zh-tw": "zh-hant",
};

/**
 * The key of a table of languages that serves a tag: the tag itself, else the
 * tag without its last subtags, one at a time (`pt-BR`, then `pt`). Keys are
 * language tags in lower case.
 */
export function languageKey(table: object, tag: string): string | undefined {
  for (let key = tag.toLowerCase(); key; key = key.slice(0, Math.max(key.lastIndexOf("-"), 0))) {
    const served = SAME_LANGUAGE[key] ?? key;
    if (Object.hasOwn(table, served)) return served;
  }
  return undefined;
}

/**
 * SurveyJS's own interface strings (button labels, validation messages): one
 * file per language, keyed here by the language tag it serves. English is built
 * in, and a language without a file reads English.
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
  ms: () => import("survey-core/i18n/malay"),
  my: () => import("survey-core/i18n/burmese"),
  nb: () => import("survey-core/i18n/norwegian"),
  nl: () => import("survey-core/i18n/dutch"),
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
  te: () => import("survey-core/i18n/telugu"),
  tg: () => import("survey-core/i18n/tajik"),
  th: () => import("survey-core/i18n/thai"),
  tr: () => import("survey-core/i18n/turkish"),
  uk: () => import("survey-core/i18n/ukrainian"),
  ur: () => import("survey-core/i18n/urdu"),
  vi: () => import("survey-core/i18n/vietnamese"),
  zh: () => import("survey-core/i18n/simplified-chinese"),
  "zh-hant": () => import("survey-core/i18n/traditional-chinese"),
};

/** The locale code a file registers its strings under in SurveyJS, where that is not the language's tag. */
const SURVEYJS_CODES: Readonly<Record<string, string>> = {
  my: "mm",
  nb: "no",
  te: "tel",
  zh: "zh-cn",
  "zh-hant": "zh-tw",
};

/** The SurveyJS locale code whose interface strings serve `tag`, if it ships any. */
export function interfaceLocale(tag: string): string | undefined {
  const key = languageKey(INTERFACE_STRINGS, tag);
  return key && (SURVEYJS_CODES[key] ?? key);
}

/**
 * Strings the pinned SurveyJS ships with another string's text. Its Spanish says
 * the answers are being saved where it means that they could not be.
 */
const CORRECTIONS: Readonly<Record<string, { key: string; shippedAs: string; text: string }>> = {
  es: { key: "savingDataError", shippedAs: "savingData", text: "Se produjo un error y no se pudieron guardar los resultados." },
};

/** Load SurveyJS's interface strings for `tag`; resolves to the locale code they registered under, if any. */
export async function loadInterfaceStrings(tag: string): Promise<string | undefined> {
  const key = languageKey(INTERFACE_STRINGS, tag);
  if (!key) return undefined;
  await INTERFACE_STRINGS[key]!();
  const code = SURVEYJS_CODES[key] ?? key;
  const strings = surveyLocalization.locales[code] as Record<string, string> | undefined;
  const correction = CORRECTIONS[code];
  // Only while the string is still the wrong one: a release that corrects it is left alone.
  if (strings && correction && strings[correction.key] === strings[correction.shippedAs]) strings[correction.key] = correction.text;
  return code;
}
