// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { Tabs, type TabsVariant } from "./tabs";

function renderTabs(variant: TabsVariant) {
  render(
    <Tabs variant={variant} defaultValue="timeline">
      <Tabs.List>
        <Tabs.Tab value="timeline">Timeline</Tabs.Tab>
        <Tabs.Tab value="related">Related</Tabs.Tab>
      </Tabs.List>
      <Tabs.Panel value="timeline">Timeline panel</Tabs.Panel>
    </Tabs>,
  );
}

const classes = (element: HTMLElement) => element.className.split(/\s+/);

test("the active pill tab carries the ring utility and its elevation shadow together", () => {
  renderTabs("pill");
  const active = screen.getByRole("tab", { name: "Timeline" });
  expect(active.hasAttribute("data-active")).toBe(true);
  // Both are Tailwind shadow utilities: the elevation writes the element's shadow layer and the
  // ring its ring layer of one composed box-shadow, so the focused active tab draws both.
  expect(classes(active)).toContain("data-[active]:shadow-xs");
  expect(classes(active)).toContain("focus-visible:focus-ring");
});

test.each(["card", "page", "pill", "rail"] as const)("the %s tab panel shows the ring on keyboard focus", (variant) => {
  renderTabs(variant);
  const panel = screen.getByRole("tabpanel");
  // Base UI puts the open panel in the tab order.
  expect(panel.getAttribute("tabindex")).toBe("0");
  expect(classes(panel)).toContain("focus-visible:focus-ring");
  // `outline-none` is bare, so the ring's forced-colours outline under `focus-visible:` outranks it.
  expect(classes(panel)).toContain("outline-none");
  expect(classes(panel).filter((name) => name.endsWith(":outline-none"))).toEqual([]);
});
