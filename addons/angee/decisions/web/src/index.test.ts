import { expectValidBaseAddon } from "@angee/app/testing";
import { decisionFixture, decisionGroupFixture, decisionResourceFixture } from "@angee/decisions/testing";
import { describe, expect, test } from "vitest";

import decisions, { DECISION_CONTENT_SLOT, DECISION_ORIGIN_SLOT, decisionContent } from "./index";

describe("decisions fragment", () => {
  test("satisfies the shared manifest contracts", () => expect(() => expectValidBaseAddon(decisions)).not.toThrow());
  test("publishes its neutral fixtures through the testing entry", () => {
    expect(decisionFixture().kind).toBe("review");
    expect(decisionResourceFixture).toBeTruthy();
    expect(decisionGroupFixture).toBeTruthy();
  });
  test("registers one routed inbox and an inherited record route", () => {
    expect(decisions.menus).toEqual([{ id: "decisions", label: "Decisions", icon: "check", route: "decisions.inbox" }]);
    expect(decisions.routes?.map(({ name, path }) => ({ name, path }))).toEqual([
      { name: "decisions.inbox", path: "/decisions" },
      { name: "decisions.inbox.record", path: "/decisions/$id" },
    ]);
    expect(decisions.routes?.[0]?.component).toBeTypeOf("function");
    expect(decisions.routes?.[0]?.indexComponent).toBeUndefined();
    expect(decisions.routes?.[1]?.component).toBeUndefined();
    expect(Object.keys(decisions.i18n ?? {})).toEqual(["decisions"]);
  });
  test("exports consumer and waiting-owner contracts without registering mandatory content", () => {
    expect(DECISION_CONTENT_SLOT).toBe("decisions.content");
    expect(DECISION_ORIGIN_SLOT).toBe("decisions.origin");
    expect(decisionContent("review", () => null)).toMatchObject({ slot: DECISION_CONTENT_SLOT, id: "review" });
    expect(decisions.slots).toBeUndefined();
  });
});
