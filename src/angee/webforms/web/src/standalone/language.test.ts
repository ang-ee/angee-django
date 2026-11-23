import { describe, expect, test } from "vitest";

import { chooseLanguage, interfaceLocale, languageKey, loadInterfaceStrings, type LanguageChoice } from "./language";

const form = { languages: ["es", "en", "pt-BR"], defaultLanguage: "es" };
const choose = (choice: Partial<LanguageChoice>) =>
  chooseLanguage({ requested: null, served: "", preferred: [], ...form, ...choice });

describe("chooseLanguage", () => {
  test("opens in the language asked for when the form is written in it", () => {
    expect(choose({ requested: "en", served: "es", preferred: ["pt-BR"] })).toBe("en");
    // A language the form is not written in is not a choice.
    expect(choose({ requested: "fr", served: "en" })).toBe("en");
  });

  test("reads the language asked for without regard to case, and answers in the form's spelling", () => {
    expect(choose({ requested: "EN", served: "es" })).toBe("en");
    expect(choose({ requested: "pt-br" })).toBe("pt-BR");
  });

  test("opens in the address's own language before the visitor's", () => {
    expect(choose({ served: "en", preferred: ["es"] })).toBe("en");
  });

  test("takes the first of the visitor's languages the form is written in, by whole tag and then by primary language", () => {
    expect(choose({ preferred: ["fr", "pt-br", "en"] })).toBe("pt-BR");
    expect(choose({ preferred: ["de-CH", "en-GB", "es"] })).toBe("en");
    expect(choose({ preferred: ["pt-PT"] })).toBe("pt-BR");
  });

  test("falls back to the form's default language", () => {
    expect(choose({ preferred: ["fr", "de"] })).toBe("es");
    expect(choose({})).toBe("es");
  });
});

describe("languageKey", () => {
  const table = { es: 1, "pt-br": 2, nb: 3, zh: 4, "zh-hant": 5 };

  test("serves a tag by itself, else by the tag without its last subtags", () => {
    expect(languageKey(table, "pt-BR")).toBe("pt-br");
    expect(languageKey(table, "es-MX")).toBe("es");
    expect(languageKey(table, "zh-Hant-TW")).toBe("zh-hant");
    expect(languageKey(table, "zh-Hans-CN")).toBe("zh");
    expect(languageKey(table, "gl")).toBeUndefined();
  });

  test("reads a tag that names a language another tag serves", () => {
    expect(languageKey(table, "no")).toBe("nb");
    expect(languageKey(table, "zh-TW")).toBe("zh-hant");
    expect(languageKey(table, "zh-HK")).toBe("zh-hant");
  });
});

describe("interfaceLocale", () => {
  test("names SurveyJS's own strings for a tag, else for its primary language, else none", () => {
    expect(interfaceLocale("es")).toBe("es");
    expect(interfaceLocale("pt-BR")).toBe("pt-br");
    expect(interfaceLocale("es-MX")).toBe("es");
    // English is built in, and a language SurveyJS ships no strings for reads it.
    expect(interfaceLocale("en")).toBeUndefined();
    expect(interfaceLocale("gl")).toBeUndefined();
  });

  test("is asked by language tag and answers with the code SurveyJS registers the strings under", () => {
    expect(interfaceLocale("te")).toBe("tel");
    expect(interfaceLocale("my")).toBe("mm");
    expect(interfaceLocale("nb")).toBe("no");
    expect(interfaceLocale("no")).toBe("no");
    expect(interfaceLocale("zh-CN")).toBe("zh-cn");
    expect(interfaceLocale("zh-TW")).toBe("zh-tw");
    // SurveyJS's own codes are not language tags.
    expect(interfaceLocale("tel")).toBeUndefined();
    expect(interfaceLocale("mm")).toBeUndefined();
  });
});

describe("loadInterfaceStrings", () => {
  test("registers a language's strings under the code it answers with", async () => {
    const { surveyLocalization } = await import("survey-core");
    for (const [tag, code] of [["te", "tel"], ["nb", "no"], ["zh-Hant", "zh-tw"], ["pt-BR", "pt-br"]] as const) {
      expect(await loadInterfaceStrings(tag)).toBe(code);
      expect(surveyLocalization.locales[code]).toBeDefined();
    }
    expect(await loadInterfaceStrings("en")).toBeUndefined();
  });

  test("Spanish says that the answers could not be kept, not that they are being saved", async () => {
    const { surveyLocalization } = await import("survey-core");
    await loadInterfaceStrings("es");
    const spanish = surveyLocalization.locales.es as Record<string, string>;
    expect(spanish.savingDataError).toBe("Se produjo un error y no se pudieron guardar los resultados.");
    expect(spanish.savingDataError).not.toBe(spanish.savingData);
  });
});
