// @vitest-environment happy-dom

import { composeAddons } from "@angee/sdk";
import notes from "@angee-example/notes-web";
import agents from "@angee/agents";
import iam from "@angee/iam";
import integrate from "@angee/integrate";
import knowledge from "@angee/knowledge";
import operator from "@angee/operator";
import storage from "@angee/storage";
import { describe, expect, test } from "vitest";

// The full addon set the host composes (mirrors main.tsx). `composeAddons` is
// fail-fast on any id collision — icon, route, menu, i18n key, widget, form,
// preview — but that check runs only at app boot, not during typecheck/build, so
// a clash between a base glyph and an addon's contribution would otherwise ship
// green and crash `angee dev`. This guard composes every addon so the gate
// catches it.
const ADDONS = [notes, iam, integrate, agents, operator, storage, knowledge];

describe("full addon composition", () => {
  test("composes every addon without an id collision", () => {
    expect(() => composeAddons(ADDONS)).not.toThrow();
  });
});
