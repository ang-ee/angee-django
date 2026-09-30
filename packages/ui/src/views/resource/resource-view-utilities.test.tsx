// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { AppRuntimeProvider } from "../../runtime";
import { ResourceViewUtilities, RESOURCE_VIEW_UTILITIES_SLOT } from "./resource-view-utilities";

afterEach(cleanup);

test("nonselection utilities remain mounted when the list has no selection", () => {
  const value = { resource: "notes.Note", fields: [], selectedIds: new Set<string>(), refresh: () => undefined };
  const ui = (selectable: boolean) => <AppRuntimeProvider runtime={{ slots: [
    { slot: RESOURCE_VIEW_UTILITIES_SLOT, id: "capture", content: <button type="button">Capture</button> },
  ] }}><ResourceViewUtilities value={{ ...value, selectable }} /></AppRuntimeProvider>;
  const rendered = render(ui(false));
  expect(screen.getByRole("button", { name: "Capture" })).toBeTruthy();
  rendered.rerender(ui(true));
  expect(screen.getByRole("button", { name: "Capture" })).toBeTruthy();
});
