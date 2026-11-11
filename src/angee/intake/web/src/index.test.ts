import { expectValidBaseAddon } from "@angee/app/testing";
import { TASK_MODEL } from "@angee/projects";
import { describe, expect, test } from "vitest";

import intake, { NEED_MODEL } from "./index";

describe("intake addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(intake)).not.toThrow();
  });

  test("leaves task access decisions to the request's writers", () => {
    expect(intake.containers?.[`${TASK_MODEL}#sections`]).toMatchObject({
      "intake.task-access-decisions": { permission: "write", requiredFields: ["permissions"] },
    });
  });

  test("exports the backend's canonical Need model label", () => {
    expect(NEED_MODEL).toBe("intake.Need");
  });
});
