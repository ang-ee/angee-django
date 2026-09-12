import { describe, expect, test } from "vitest";
import {
  InboxFilter,
  inboxCollectionQuery,
  navigatorAxes,
  resultLensForGroup,
} from "./contract";

describe("the explorer control contract", () => {
  test("all navigator lenses declare only their applicable axes", () => {
    expect(navigatorAxes("senders")).toEqual([
      "recency",
      "circle",
      "group",
      "platform",
      "account",
      "link",
      "organization",
    ]);
    expect(navigatorAxes("recipients")).toEqual(navigatorAxes("handles"));
    expect(navigatorAxes("groups")).toEqual(["recency", "platform", "account"]);
    expect(navigatorAxes("circles")).toEqual([]);
  });
  test("grouping and message lenses never contradict each other", () => {
    expect(resultLensForGroup("conversations", undefined)).toBe("timeline");
    expect(resultLensForGroup("timeline", { field: "by_conversation" })).toBe(
      "conversations",
    );
    expect(
      resultLensForGroup("attachments", { field: "by_conversation" }),
    ).toBe("attachments");
    expect(resultLensForGroup("text", { field: "by_account" })).toBe("text");
  });
  test("coverage ORs field values and ANDs different fields without changing text-role search", () => {
    const value = new InboxFilter({
      platform: { inList: ["email", "telegram"] },
      account: { exact: "acc_one" },
      role: { exact: "quoted" },
      text: { iContains: 'invoice "next week"' },
    });
    expect(value.coverage("Europe/Prague")).toMatchObject({
      platforms: ["email", "telegram"],
      accounts: ["acc_one"],
      timezone: "Europe/Prague",
    });
    expect(value.search()).toMatchObject({
      text: 'invoice "next week"',
      quoted: false,
    });
    expect(value.values("role")).toEqual(["quoted"]);
  });
  test("contradictory or unsupported Boolean input cannot silently widen results", () => {
    expect(() =>
      new InboxFilter({ AND: [{ account: "a" }, { account: "b" }] }).coverage(
        "UTC",
      ),
    ).toThrow("conflicting account");
    expect(
      () => new InboxFilter({ OR: [{ account: "a" }, { account: "b" }] }),
    ).toThrow("AND predicates");
    expect(() =>
      new InboxFilter({ by_account: { exact: "a", isNull: true } }).scope(),
    ).toThrow("conflicting group");
  });
  test("semantic group scopes remain distinct from coverage fields", () => {
    const value = new InboxFilter({ account: "one", by_account: "two" });
    expect(value.coverage("UTC").accounts).toEqual(["one"]);
    expect(value.scope()).toEqual({ axis: "account", value: "two" });
    expect(Object.keys(inboxCollectionQuery(["account"]).axes)).toEqual([
      "by_account",
    ]);
  });
});
