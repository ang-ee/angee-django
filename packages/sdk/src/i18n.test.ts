import { describe, expect, test } from "vitest";

import { interpolateMessage, translateWithFallback } from "./i18n";

describe("interpolateMessage", () => {
  test("substitutes named placeholders", () => {
    expect(interpolateMessage("Hi {name}, you have {count} notes", {
      name: "Ada",
      count: 3,
    })).toBe("Hi Ada, you have 3 notes");
  });

  test("leaves unknown placeholders untouched", () => {
    expect(interpolateMessage("Hi {name}", {})).toBe("Hi {name}");
  });
});

describe("translateWithFallback", () => {
  const hostT = (key: string) => (key === "shared.save" ? "Save" : key);

  test("prefers a host translation when the host resolves the key", () => {
    expect(translateWithFallback(hostT, { "shared.save": "ignored" }, "shared.save")).toBe(
      "Save",
    );
  });

  test("falls back to addon messages when the host echoes the key", () => {
    expect(
      translateWithFallback(hostT, { "notes.title": "Title" }, "notes.title"),
    ).toBe("Title");
  });

  test("falls back to the key itself when nothing resolves it", () => {
    expect(translateWithFallback(hostT, {}, "notes.missing")).toBe("notes.missing");
  });

  test("interpolates variables into the resolved message", () => {
    expect(
      translateWithFallback(hostT, { "notes.greet": "Hi {name}" }, "notes.greet", {
        name: "Ada",
      }),
    ).toBe("Hi Ada");
  });
});
