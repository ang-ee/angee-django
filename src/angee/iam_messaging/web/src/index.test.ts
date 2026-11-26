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

  test("offers Send sign-in link among IAM's account verbs, read from the row's projection", () => {
    const verbs = iamMessaging.containers?.["iam.User#actions-menu"] as Record<string, { requiredFields?: readonly string[] }>;
    expect(Object.keys(verbs)).toEqual(["iam_messaging.send-sign-in-link"]);
    expect(verbs["iam_messaging.send-sign-in-link"]?.requiredFields).toEqual(["account_actions", "revision", "username"]);
    expect(iam.containers?.["iam.User#actions-menu"]).not.toHaveProperty("iam.issue-sign-in-link");
  });
});
