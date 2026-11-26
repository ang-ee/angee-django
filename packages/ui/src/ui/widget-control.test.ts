import { expect, test } from "vitest";

import { WIDGET_CONTROL_DATA_READONLY_CLASS, interactiveSurface, widgetControlSurface } from "./widget-control";

const PSEUDOS = { self: "focus", visible: "focus-visible", within: "focus-within" } as const;
const classes = (value: string) => value.split(/\s+/);

// The ring utility draws the forced-colours outline under its state variant; the shared layer hides
// the native outline with a bare `outline-none`, which that variant outranks by specificity.
test.each(Object.entries(PSEUDOS))("focus %s draws the ring under :%s beside a bare outline-none", (focus, pseudo) => {
  for (const recipe of [interactiveSurface({ focus: focus as keyof typeof PSEUDOS }), widgetControlSurface({ focus: focus as keyof typeof PSEUDOS })]) {
    const names = classes(recipe);
    expect(names).toContain(`${pseudo}:focus-ring`);
    expect(names).toContain("outline-none");
    expect(names.filter((name) => name.endsWith(":outline-none"))).toEqual([]);
  }
});

test.each(Object.entries(PSEUDOS))("an invalid control focused by %s draws the danger ring under the same :%s", (focus, pseudo) => {
  const names = classes(widgetControlSurface({ focus: focus as keyof typeof PSEUDOS, invalid: true }));
  expect(names).toContain(`${pseudo}:focus-ring-danger`);
});

test("a control read-only by its data attribute keeps no ring: it drops the ring layer and the outline, not only the shadow", () => {
  const names = classes(WIDGET_CONTROL_DATA_READONLY_CLASS);
  expect(names).toContain("data-[readonly]:shadow-none");
  expect(names).toContain("data-[readonly]:ring-0");
  expect(names).toContain("data-[readonly]:outline-none");
});
