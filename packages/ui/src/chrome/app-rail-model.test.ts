import { describe, expect, test, vi } from "vitest";
import type { MouseEvent } from "react";

import {
  activeLinkToggleProps,
  moveRailItem,
  orderedRailItems,
  railDefaultTarget,
  railSortableMove,
  resolvedRailExpanded,
  sameRailOrder,
} from "./app-rail-model";

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
    expect(activeLinkToggleProps("/notes", "/notes", undefined, true)).toEqual({});
    expect(activeLinkToggleProps(undefined, "/notes", toggle, true)).toEqual({});
    expect(activeLinkToggleProps("/notes", "/notes/archive", toggle, true))
      .toEqual({});

    const props = activeLinkToggleProps("/notes", "/notes", toggle, false);
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
