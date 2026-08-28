import { expectValidBaseAddon } from "@angee/app/testing";
import { PROJECT_MODEL, TASK_MODEL } from "@angee/projects";
import { describe, expect, test } from "vitest";

import intake, { NEED_MODEL } from "./index";

describe("intake addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(intake)).not.toThrow();
  });

  test("contributes one form-section pane to project and task records", () => {
    expect((intake.slots ?? []).map(({ id, model, slot }) => [id, model, slot])).toEqual([
      ["intake.project-needs", PROJECT_MODEL, "form-view.sections"],
      ["intake.task-needs", TASK_MODEL, "form-view.sections"],
    ]);
    expect(intake.routes ?? []).toEqual([]);
    expect(intake.menus ?? []).toEqual([]);
  });

  test("exports the canonical Need resource key and its pane glyph", () => {
    expect(NEED_MODEL).toBe("intake.Need");
    expect(intake.icons?.["intake-needs"]).toBeDefined();
  });
});
