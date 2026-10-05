import { describe, expect, test, vi } from "vitest";
import type { MouseEvent } from "react";

import { HOME_PATH_PREFERENCE_KEY } from "../runtime";
import {
  landingTarget,
  railLinkToggleProps,
  moveRailItem,
  orderedRailItems,
  railDefaultTarget,
  railSortableMove,
  resolvedRailExpanded,
  sameRailOrder,
} from "./app-rail-model";
import { APP_RAIL_PREFERENCES_KEY } from "./app-rail-preferences";
import { MenuTree } from "./menu-tree";

const ITEMS = [
  { id: "notes", target: "/notes" },
  { id: "ops", target: "/ops" },
  { id: "integrate", target: "/integrate" },
];

describe("app rail model", () => {
  test("orders known ids first and appends new items", () => {
    expect(
      orderedRailItems(ITEMS, ["ops", "settings", "missing", "ops"]).map((item) => item.id),
    ).toEqual(["ops", "notes", "integrate"]);
  });

  test("moves items before and after a target", () => {
    expect(moveRailItem(["notes", "ops", "integrate"], "integrate", "notes", "before"))
      .toEqual(["integrate", "notes", "ops"]);
    expect(moveRailItem(["notes", "ops", "integrate"], "notes", "ops", "after"))
      .toEqual(["ops", "notes", "integrate"]);
  });

  test("derives sortable placement from item direction", () => {
    expect(railSortableMove(["notes", "ops", "integrate"], "notes", "integrate"))
      .toEqual(["ops", "integrate", "notes"]);
    expect(railSortableMove(["notes", "ops", "integrate"], "integrate", "notes"))
      .toEqual(["integrate", "notes", "ops"]);
  });

  test("resolves default targets", () => {
    expect(railDefaultTarget({ target: " /notes " })).toBe("/notes");
    expect(railDefaultTarget({ target: "#" })).toBeNull();
  });

  test("lands on the saved home, the rail default, the declared home, the ordered rail, then Settings", () => {
    const settings = { id: "platform", group: "platform" as const, to: "/platform/graph" };
    // Settings is declared first and a hidden app second: neither is the first rail app.
    const tree = MenuTree.from([settings, { id: "hidden", to: "/hidden", hidden: true }, { id: "notes", to: "/notes" }, { id: "ops", to: "/ops" }]);
    const rail = (value: object) => ({ [APP_RAIL_PREFERENCES_KEY]: value });

    expect(landingTarget(tree, { [HOME_PATH_PREFERENCE_KEY]: "/dashboards/7", ...rail({ defaultItemId: "ops" }) }, "/declared"))
      .toBe("/dashboards/7");
    expect(landingTarget(tree, { [HOME_PATH_PREFERENCE_KEY]: "relative", ...rail({ defaultItemId: "ops" }) }, "/declared"))
      .toBe("/ops");
    expect(landingTarget(tree, rail({ order: ["ops"], defaultItemId: "missing" }), "/declared")).toBe("/declared");
    expect(landingTarget(tree, rail({ order: ["ops"] }))).toBe("/ops");
    expect(landingTarget(tree, {})).toBe("/notes");
    expect(landingTarget(MenuTree.from([settings]), {})).toBe("/platform/graph");
    expect(landingTarget(MenuTree.from([]), {})).toBeNull();
  });

  test("toggles only on a plain second click of the current page's link", () => {
    const toggle = vi.fn();
    const clickEvent = (init: Partial<MouseEvent<HTMLElement>> = {}) => ({
      defaultPrevented: false,
      metaKey: false,
      ctrlKey: false,
      shiftKey: false,
      altKey: false,
      button: 0,
      preventDefault: vi.fn(),
      ...init,
    } as unknown as MouseEvent<HTMLElement>);

    // No toggle, no target, or a different target → inert props.
    expect(railLinkToggleProps("/notes", "/notes", undefined, true)).toEqual({});
    expect(railLinkToggleProps(undefined, "/notes", toggle, true)).toEqual({});
    expect(railLinkToggleProps("/notes", "/notes/archive", toggle, true))
      .toEqual({});

    const props = railLinkToggleProps("/notes", "/notes", toggle, false);
    expect(props["aria-expanded"]).toBe(false);

    // Modified and non-primary clicks keep the browser default (new tab).
    for (const init of [
      { metaKey: true },
      { ctrlKey: true },
      { shiftKey: true },
      { altKey: true },
      { button: 1 },
      { defaultPrevented: true },
    ]) {
      const event = clickEvent(init);
      props.onClick!(event);
      expect(event.preventDefault).not.toHaveBeenCalled();
    }
    expect(toggle).not.toHaveBeenCalled();

    // A plain left-click toggles instead of navigating.
    const plain = clickEvent();
    props.onClick!(plain);
    expect(plain.preventDefault).toHaveBeenCalledTimes(1);
    expect(toggle).toHaveBeenCalledTimes(1);

    const openNavigation = vi.fn();
    const temporary = railLinkToggleProps("/notes", "/projects", toggle, true, openNavigation);
    expect(temporary).toMatchObject({ "aria-haspopup": "dialog" });
    expect(temporary).not.toHaveProperty("aria-expanded");
  });

  test("compares canonical order arrays", () => {
    expect(sameRailOrder(["notes", "ops"], ["notes", "ops"])).toBe(true);
    expect(sameRailOrder(["notes", "ops"], ["ops", "notes"])).toBe(false);
  });

  test("applies expansion preferences only at the large viewport", () => {
    expect(resolvedRailExpanded(undefined, true)).toBe(true);
    expect(resolvedRailExpanded(false, true)).toBe(false);
    expect(resolvedRailExpanded(true, true)).toBe(true);
    expect(resolvedRailExpanded(true, false)).toBe(false);
  });
});
