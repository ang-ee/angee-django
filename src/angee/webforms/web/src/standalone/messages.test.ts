import { describe, expect, test } from "vitest";

import { PAGE_LANGUAGES, pageMessages } from "./messages";

describe("pageMessages", () => {
  test("speaks the first language it has sentences in, by whole tag and then by its language", () => {
    expect(pageMessages(["es"]).closed).toBe("Este formulario no está aceptando respuestas.");
    expect(pageMessages(["gl", "pt-BR"]).language).toBe("Idioma");
    expect(pageMessages(["pt-BR"]).closed).toBe("Este formulário não está aceitando respostas.");
    expect(pageMessages(["pt-PT"]).closed).toBe("Este formulário não está a aceitar respostas.");
    expect(pageMessages(["zh-TW"]).language).toBe("語言");
    expect(pageMessages(["no"]).language).toBe("Språk");
  });

  test("reads English where it has no sentences, or no language was given", () => {
    expect(pageMessages(["gl"]).closed).toBe("This form is not taking responses.");
    expect(pageMessages([null, undefined, ""]).language).toBe("Language");
  });

  test("says what happened when the form changed, and not to reload", () => {
    expect(pageMessages(["en"]).changed)
      .toBe("The form changed while you were filling it in. Your answers are kept; please check them and send again.");
  });

  test("every language has every sentence", () => {
    for (const tag of PAGE_LANGUAGES) {
      const messages = pageMessages([tag]);
      expect(Object.keys(messages).sort()).toEqual(["changed", "closed", "language", "unavailable"]);
      for (const text of Object.values(messages)) expect(text.trim().length).toBeGreaterThan(1);
    }
  });
});
