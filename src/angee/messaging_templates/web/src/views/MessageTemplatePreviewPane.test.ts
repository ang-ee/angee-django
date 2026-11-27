import { describe, expect, test, vi } from "vitest";
import type { FormNextSave } from "@angee/ui";

import { previewIssues, unsavedEdits } from "./MessageTemplatePreviewPane";

/** A save surface as the pane reads it: whether the form is dirty and takes edits, and what its Save would send. */
function surface(formIsDirty: boolean, formTakesEdits: boolean, data: FormNextSave["data"]) {
  return { formIsDirty, formTakesEdits, nextSave: vi.fn((): FormNextSave => ({ data, lines: null })) };
}

describe("unsavedEdits", () => {
  test("is what Save would send, as text, for a dirty form that takes edits", () => {
    expect(unsavedEdits(surface(true, true, { subject: "Edited" }))).toBe('{"subject":"Edited"}');
  });

  test("is none for a dirty form whose Save would write nothing", () => {
    expect(unsavedEdits(surface(true, true, {}))).toBeNull();
  });

  test("is none, unread, for a form that holds edits but takes none, as when a permission is withdrawn", () => {
    const withdrawn = surface(true, false, { subject: "Edited" });
    expect(unsavedEdits(withdrawn)).toBeNull();
    expect(withdrawn.nextSave).not.toHaveBeenCalled();
  });

  test("is none, unread, for a form that holds no edit", () => {
    const clean = surface(false, true, { subject: "Edited" });
    expect(unsavedEdits(clean)).toBeNull();
    expect(clean.nextSave).not.toHaveBeenCalled();
  });
});

describe("previewIssues", () => {
  test("gives one issue per column when the same words come under two", () => {
    const issues = previewIssues([
      { code: "SAVE_REFUSED", detail: "Give an object.", field: "variables" },
      { code: "SAVE_REFUSED", detail: "Give an object.", field: "sample" },
      { code: "SAVE_REFUSED", detail: "An HTML message template has a plain-text form.", field: null },
    ], false);
    expect(issues.map((issue) => [issue.message, issue.field, issue.tone])).toEqual([
      ["Give an object.", "variables", "danger"],
      ["Give an object.", "sample", "danger"],
      ["An HTML message template has a plain-text form.", undefined, "danger"],
    ]);
    expect(new Set(issues.map((issue) => issue.id)).size).toBe(issues.length);
  });

  test("tells a rendered row's warnings apart from the reasons nothing renders by tone", () => {
    const warning = { code: "PREHEADER" as const, detail: "The preheader is missing.", field: null };
    expect(previewIssues([warning], true).map((issue) => issue.tone)).toEqual(["warning"]);
    expect(previewIssues([warning], false).map((issue) => issue.tone)).toEqual(["danger"]);
  });
});
