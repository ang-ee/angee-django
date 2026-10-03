import { expectValidBaseAddon } from "@angee/app/testing";
import { decisionFixture, decisionGroupFixture, decisionResourceFixture } from "@angee/decisions/testing";
import { describe, expect, test } from "vitest";

import decisions, { DECISION_MODEL, decisionContent, decisionRecordTab } from "./index";

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
    expect(DECISION_MODEL).toBe("decisions.Decision");
    // The addon declares its containers empty; contributors name their `decisions#content` children.
    expect(decisions.containers).toEqual({ "decisions#content": { unique: "key" }, "decisions#origin": {} });
    expect(decisionContent("review", () => null)).toMatchObject({ key: "review", content: expect.anything() });
    // A subject model's addon declares it at `<model>#sections` under its own id.
    expect(decisionRecordTab()).toMatchObject({ sequence: 50, content: expect.anything() });
  });
});
