// @vitest-environment happy-dom

import { cleanup, fireEvent, render, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { ThemePreviewFrame } from "./preview";

afterEach(cleanup);

function renderPreview(variant: "full" | "card", colorScheme: "light" | "dark") {
  const title = `${variant} ${colorScheme} preview`;
  const view = render(<ThemePreviewFrame title={title} themeId={null} colorScheme={colorScheme} variant={variant} />);
  const frame = view.getByTitle(title) as HTMLIFrameElement;
  // happy-dom clears native iframe bodies before React can unmount their portals.
  const target = document.implementation.createHTMLDocument(title);
  Object.defineProperty(target, "defaultView", { value: window });
  Object.defineProperty(frame, "contentDocument", { value: target });
  fireEvent.load(frame);
  expect(target.documentElement.dataset.colorScheme).toBe(colorScheme);
  return within(target.body);
}

function chromeRegions(preview: ReturnType<typeof within>) {
  const rail = preview.getByRole("complementary", { name: "App rail" });
  expect(within(rail).getByRole("img", { name: "Logo" })).toBeTruthy();
  expect(within(rail).getAllByRole("img")).toHaveLength(4);
  expect(within(rail).getByRole("img", { name: "Workspace app" }).getAttribute("aria-current")).toBe("page");
  expect(rail.textContent).toBe("");
  const topBar = preview.getByRole("banner", { name: "Top bar" });
  const menu = within(topBar).getByRole("navigation", { name: "Workspace menu" });
  expect(menu.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
  const controls = preview.getByRole("group", { name: "Control band" });
  const page = preview.getByRole("region", { name: "Page samples" });
  expect(rail.contains(topBar)).toBe(false);
  expect(topBar.contains(controls)).toBe(false);
  expect(page.contains(controls)).toBe(false);
  expect(topBar.compareDocumentPosition(controls) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(controls.compareDocumentPosition(page) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  return { topBar, menu, controls, page };
}

test.each(["light", "dark"] as const)("full %s preview separates app chrome, page controls and page samples", (scheme) => {
  const { topBar, menu, controls, page } = chromeRegions(renderPreview("full", scheme));
  expect(within(topBar).getByText("Workspace")).toBeTruthy();
  expect(within(menu).getByText("Overview").getAttribute("aria-current")).toBe("page");
  expect(within(menu).getByText("Projects").hasAttribute("aria-current")).toBe(false);
  expect(within(menu).getByText("Reports").hasAttribute("aria-current")).toBe(false);

  expect(within(controls).getByRole("button", { name: "Primary action" })).toBeTruthy();
  expect(within(controls).getByRole("button", { name: "Secondary" })).toBeTruthy();
  expect(within(page).getByRole("heading", { name: "Workspace overview" })).toBeTruthy();
  expect(within(page).getByRole("button", { name: "Quiet action" })).toBeTruthy();
  expect(within(page).getByText("On track")).toBeTruthy();
  expect(within(page).getByText("Needs review")).toBeTruthy();
  const card = within(page).getByRole("article");
  expect(within(card).getByRole("heading", { name: "Project details" })).toBeTruthy();
  expect(within(card).getByRole("textbox", { name: "Project name" })).toBeTruthy();
  expect(within(card).getByRole("textbox", { name: "Owner" })).toBeTruthy();
  expect(within(card).getByRole("checkbox", { name: "Send a weekly summary" })).toBeTruthy();
  const table = within(page).getByRole("table");
  expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Item", "Status", "Value"]);
  expect(within(table).getByRole("row", { name: "Design review Active 72%" })).toBeTruthy();
  expect(within(table).getByRole("row", { name: "Release prep Queued 18%" })).toBeTruthy();
});

test.each(["light", "dark"] as const)("card %s preview keeps the L-shaped landmarks without visible text or controls", (scheme) => {
  const preview = renderPreview("card", scheme);
  chromeRegions(preview);
  expect(preview.getByRole("main").textContent).toBe("");
  expect(preview.queryByRole("button")).toBeNull();
});
