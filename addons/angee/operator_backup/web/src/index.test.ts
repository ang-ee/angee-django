import { composeAddons, defineBaseAddon } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import { expect, test } from "vitest";
import backup from "./index";

/** Exercise the real compiler: foreign-owner menu keys can typecheck yet fail at application boot. */
test("backup routes and menus compose beneath the existing Operator app", () => {
  const operator = defineBaseAddon({ id: "operator", menus: { operator: { label: "Operator", group: "platform" } } });
  expect(() => expectValidBaseAddon(backup)).not.toThrow();
  const composed = composeAddons([operator, { ...backup, dependsOn: ["operator"] }], { canonicalModelLabel: (label) => label });
  const root = composed.menus.find((menu) => menu.id === "operator");
  expect(root?.children?.map((menu) => menu.route)).toEqual(["operator.backup-plans", "operator.backups"]);
  expect(composed.routes.find((route) => route.name === "operator.backup-plans")?.path).toBe("/operator/backup-plans");
  expect(composed.routes.find((route) => route.name === "operator.backups.record")?.parent).toBe("operator.backups");
});
