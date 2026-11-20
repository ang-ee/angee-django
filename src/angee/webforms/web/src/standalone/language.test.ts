import { describe, expect, test } from "vitest";

import { chooseLanguage, interfaceLocale, type LanguageChoice } from "./language";

const form = { languages: ["es", "en", "pt-BR"], defaultLanguage: "es" };
const choose = (choice: Partial<LanguageChoice>) =>
  chooseLanguage({ requested: null, served: "", preferred: [], ...form, ...choice });

describe("chooseLanguage", () => {
  test("opens in the language asked for when the form is written in it", () => {
    expect(choose({ requested: "en", served: "es", preferred: ["pt-BR"] })).toBe("en");
    // A language the form is not written in is not a choice.
    expect(choose({ requested: "fr", served: "en" })).toBe("en");
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

describe("interfaceLocale", () => {
  test("names SurveyJS's own strings for a tag, else for its primary language, else none", () => {
    expect(interfaceLocale("es")).toBe("es");
    expect(interfaceLocale("pt-BR")).toBe("pt-br");
    expect(interfaceLocale("es-MX")).toBe("es");
    // English is built in, and a language SurveyJS ships no strings for reads it.
    expect(interfaceLocale("en")).toBeUndefined();
    expect(interfaceLocale("gl")).toBeUndefined();
  });
});
