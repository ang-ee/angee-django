// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { AppRuntimeProvider, containersFromChildren } from "../../runtime";
import { RESOURCE_CONTAINERS } from "./resource-view-kinds";
import { ResourceViewUtilities } from "./resource-view-utilities";

afterEach(cleanup);

const containers = containersFromChildren(RESOURCE_CONTAINERS, {
  "notes.Note#utilities": { "notes.capture": { content: <button type="button">Capture</button> } },
  "tasks.Task#utilities": { "tasks.triage": { content: <button type="button">Triage</button> } },
});

test("nonselection utilities remain mounted when the list has no selection", () => {
  const value = { resource: "notes.Note", fields: [], selectedIds: new Set<string>(), refresh: () => undefined };
  const ui = (selectable: boolean) => <AppRuntimeProvider runtime={{ containers }}>
    <ResourceViewUtilities value={{ ...value, selectable }} />
  </AppRuntimeProvider>;
  const rendered = render(ui(false));
  expect(screen.getByRole("button", { name: "Capture" })).toBeTruthy();
  rendered.rerender(ui(true));
  expect(screen.getByRole("button", { name: "Capture" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Triage" })).toBeNull();
});
