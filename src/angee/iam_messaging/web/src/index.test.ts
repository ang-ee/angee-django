import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import iam from "@angee/iam";
import { describe, expect, test } from "vitest";

import iamMessaging from "./index";

describe("iam_messaging addon manifest", () => {
  test("satisfies the rendered-addon invariants and composes beside IAM", () => {
    expect(() => expectValidBaseAddon(iamMessaging)).not.toThrow();
    expect(() => composeAddons([iam, iamMessaging], { canonicalModelLabel: (label) => label })).not.toThrow();
  });

  test("opens sign-in links on a public route outside the console chrome", () => {
    expect(iamMessaging.routes?.find((route) => route.name === "iam_messaging.sign-in-link")).toMatchObject({
      path: "/sign-in-link",
      layout: "public",
    });
  });

  test("offers Send sign-in link among IAM's account actions, read from the row's projection", () => {
    const actions = iamMessaging.containers?.["iam.User#actions-menu"] as Record<string, { requiredFields?: readonly string[] }>;
    expect(Object.keys(actions)).toEqual(["iam_messaging.issue-sign-in-link"]);
    expect(actions["iam_messaging.issue-sign-in-link"]?.requiredFields).toEqual(["account_actions", "revision", "username"]);
    expect(iam.containers?.["iam.User#actions-menu"]).not.toHaveProperty("iam.issue-sign-in-link");
  });
});
