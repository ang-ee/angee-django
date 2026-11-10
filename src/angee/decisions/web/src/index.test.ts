import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import decisions, { DECISION_MODEL } from "./index";

describe("decisions fragment", () => {
  test("satisfies the shared manifest contracts", () => expect(() => expectValidBaseAddon(decisions)).not.toThrow());
  test("exports the backend's canonical Decision model label to its consumers", () => {
    expect(DECISION_MODEL).toBe("decisions.Decision");
  });
});
