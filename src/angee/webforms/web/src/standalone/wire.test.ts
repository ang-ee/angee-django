import { describe, expect, test } from "vitest";

import { newSubmissionId, readDescription, readRefusal } from "./wire";

const DESCRIPTION = {
  version: 3,
  definition: { pages: [] },
  languages: ["es", "en"],
  default_language: "es",
  language: "",
  theme: "",
  honeypot_field: "website",
};

describe("readDescription", () => {
  test("reads what the address describes", () => {
    expect(readDescription({ ...DESCRIPTION, extra: true })).toEqual(DESCRIPTION);
  });

  test.each([
    ["nothing", undefined],
    ["a page of markup", "<!doctype html>"],
    ["a list", [DESCRIPTION]],
    ["a version that is not a number", { ...DESCRIPTION, version: "3" }],
    ["a definition that is not an object", { ...DESCRIPTION, definition: [] }],
    ["languages that are not texts", { ...DESCRIPTION, languages: ["es", 1] }],
    ["a default language the form is not written in", { ...DESCRIPTION, default_language: "fr" }],
    ["no honeypot field", { ...DESCRIPTION, honeypot_field: undefined }],
  ])("refuses %s", (_label, value) => {
    expect(() => readDescription(value)).toThrow(TypeError);
  });
});

describe("readRefusal", () => {
  test("keeps a message and the refusals by question", () => {
    expect(readRefusal({ error: "invalid_submission", message: "No.", fields: { name: ["Too long."] } }))
      .toEqual({ message: "No.", fields: { name: ["Too long."] } });
  });

  test("keeps nothing of an answer with another shape", () => {
    expect(readRefusal(undefined)).toEqual({});
    expect(readRefusal("Bad gateway")).toEqual({});
    expect(readRefusal({ message: 7, fields: ["name"] })).toEqual({});
    expect(readRefusal({ message: "", fields: { name: "Too long.", size: ["Pick one.", 2] } })).toEqual({});
  });
});

describe("newSubmissionId", () => {
  test("is within the server's rule for a submission id, and new each time", () => {
    const ids = [newSubmissionId(), newSubmissionId()];
    for (const id of ids) expect(id).toMatch(/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/);
    expect(ids[0]).toHaveLength(32);
    expect(ids[0]).not.toBe(ids[1]);
  });
});
