import { expectValidBaseAddon } from "@angee/app/testing";
import { PROJECT_MODEL, TASK_MODEL } from "@angee/projects";
import { describe, expect, test } from "vitest";

import intake, { NEED_MODEL } from "./index";

describe("intake addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(intake)).not.toThrow();
  });

  test("contributes one form-section pane to project and task records", () => {
    const children = Object.fromEntries(Object.entries(intake.containers ?? {}).map(([address, entry]) =>
      [address, Object.keys(entry as object)]));
    expect(children).toEqual({
      [`${TASK_MODEL}#access-roles`]: ["intake.requester"],
      [`${TASK_MODEL}#aside`]: ["intake.access-decisions"],
      [`${TASK_MODEL}#rail`]: ["intake.people-rail"],
      [`${TASK_MODEL}#sections`]: ["intake.task-access-decisions", "intake.task-needs"],
      [`${PROJECT_MODEL}#sections`]: ["intake.project-needs"],
    });
    // Access decisions are the request's writers' business.
    expect(intake.containers?.[`${TASK_MODEL}#sections`]).toMatchObject({
      "intake.task-access-decisions": { permission: "write", requiredFields: ["permissions"] },
    });
    expect(intake.routes ?? []).toEqual([]);
    expect(intake.menus ?? []).toEqual([]);
  });

  test("exports the canonical Need resource key and its pane glyph", () => {
    expect(NEED_MODEL).toBe("intake.Need");
    expect(intake.icons?.["intake-needs"]).toBeDefined();
  });

  test("offers the same actor-scoped access projection in the task aside, on record views only", () => {
    const tab = (intake.containers?.[`${TASK_MODEL}#aside`] as Record<string, { sequence?: number; content: { when?: (context: unknown) => boolean } }>)["intake.access-decisions"];
    expect(tab?.sequence).toBe(50);
    const view = (kind: string) => ({ pathname: "/", params: {}, view: { kind, type: "projects/task" } });
    expect(tab?.content.when?.(view("record"))).toBe(true);
    expect(tab?.content.when?.(view("list"))).toBe(false);
  });
});
