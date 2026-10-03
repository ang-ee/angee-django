// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { ModelMetadataProvider, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";
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

test("a list over its own source keeps kind-level utilities without looking up a model", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const kindLevel = containersFromChildren(RESOURCE_CONTAINERS, {
    "resource#utilities": { "notes.export": { content: <button type="button">Export</button> } },
  });
  const metadata = schemaFieldMetadataFromDataResources([testDataResource("notes.Note")]);
  const value = { resource: "inbox.Results", fields: [], refresh: () => undefined, selectable: false };
  const ui = (modelBacked: boolean) => <ModelMetadataProvider metadata={metadata}>
    <AppRuntimeProvider runtime={{ containers: kindLevel }}>
      <ResourceViewUtilities value={value} modelBacked={modelBacked} />
    </AppRuntimeProvider>
  </ModelMetadataProvider>;
  render(ui(false));
  expect(screen.getByRole("button", { name: "Export" })).toBeTruthy();
  expect(warn).not.toHaveBeenCalled();
  cleanup();
  // A model-backed list naming an unknown model still warns, as every other lookup does.
  render(ui(true));
  expect(warn).toHaveBeenCalledWith(expect.stringMatching(/model metadata lookup.*inbox\.Results/));
  warn.mockRestore();
});
