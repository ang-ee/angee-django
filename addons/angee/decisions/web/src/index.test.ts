import { expectValidBaseAddon } from "@angee/app/testing";
import { decisionFixture, decisionResourceFixture } from "@angee/decisions/testing";
import { describe, expect, test } from "vitest";

import decisions, { DECISION_MODEL, DecisionCard, fieldsToMark } from "./index";

describe("decisions fragment", () => {
  test("satisfies the shared manifest contracts", () => expect(() => expectValidBaseAddon(decisions)).not.toThrow());
  test("publishes its neutral fixtures through the testing entry", () => {
    expect(decisionFixture().kind).toBe("review");
    expect(decisionResourceFixture).toBeTruthy();
  });
  test("registers one routed inbox and an inherited record route", () => {
    expect(decisions.menus).toMatchObject({
      decisions: { label: "Decisions" },
      "decisions.queue": { parent: "decisions", label: "Decisions" },
      "decisions.inbox": { parent: "decisions.queue", route: "decisions.inbox", defaultResourceView: "decisions.inbox" },
      "decisions.waiting": { parent: "decisions.queue", label: "Waiting on me", route: "decisions.inbox", defaultResourceView: "decisions.waiting" },
      "decisions.all": { parent: "decisions.queue", route: "decisions.inbox", defaultResourceView: "decisions.all" },
    });
    expect(decisions.routes?.map(({ name, path }) => ({ name, path }))).toEqual([
      { name: "decisions.inbox", path: "/decisions" },
      { name: "decisions.inbox.record", path: "/decisions/$id" },
    ]);
    expect(decisions.routes?.[0]?.component).toBeTypeOf("function");
    expect(decisions.routes?.[0]?.indexComponent).toBeUndefined();
    expect(decisions.routes?.[1]?.component).toBeUndefined();
    expect(decisions.routes?.[0]?.defaultResourceView).toBe("decisions.inbox");
    expect(decisions.resourceViews).toContainEqual({ id: "decisions.waiting", label: "Waiting on me", resource: DECISION_MODEL,
      filter: { is_open: { exact: true }, can_act: { exact: true } } });
    expect(Object.keys(decisions.i18n ?? {})).toEqual(["decisions"]);
  });
  test("exports consumer and waiting-owner contracts without registering mandatory content", () => {
    expect(DECISION_MODEL).toBe("decisions.Decision");
    // Independent waiters contribute origin links through the declared container.
    expect(decisions.containers).toEqual({ "decisions#origin": {} });
    expect(DecisionCard).toBeTypeOf("function");
    expect(fieldsToMark([], "nte_7")).toEqual([]);
  });
});
