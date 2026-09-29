// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, expect, test, vi } from "vitest";

import { Catalogue, CatalogueStory, QueryError, Unavailable } from "./WorkflowsPage.stories";

beforeAll(() => { Element.prototype.getAnimations ??= () => []; });
afterEach(cleanup);

test("opens a workflow from the catalogue with its retained versions and recent runs", async () => {
  render(Catalogue.render());
  const workflow = await screen.findByRole("link", { name: /record_review/ });
  expect(new URL(workflow.getAttribute("href")!, "https://example.test").pathname).toBe("/workflows/wfl_review");
  expect(screen.queryByRole("button", { name: /New/ })).toBeNull();
  fireEvent.click(workflow);
  expect(await screen.findByRole("heading", { level: 1, name: "Record review" })).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Versions" }));
  expect(await screen.findByText("retained_hash")).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Recent runs" }));
  expect(await screen.findByText("Failed")).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
  expect(await screen.findByText("A retained review process.")).toBeTruthy();
});

test("versions and recent runs are filtered by the open workflow using native list transport", async () => {
  const onRequest = vi.fn();
  render(<CatalogueStory onRequest={onRequest} />);
  fireEvent.click(await screen.findByRole("tab", { name: "Versions" }));
  await screen.findByText("retained_hash");
  fireEvent.click(screen.getByRole("tab", { name: "Recent runs" }));
  await screen.findByText("Failed");
  for (const name of ["workflowversion", "workflowrun"]) {
    const request = onRequest.mock.calls.find(([entry]) => entry.query.includes(`${name}(`))?.[0];
    expect(request?.variables.where).toEqual({ _and: [
      ...(name === "workflowrun" ? [{ status: { _eq: "failed" } }] : []),
      { [name === "workflowrun" ? "version__workflow" : "workflow"]: { _eq: "wfl_review" } },
    ] });
  }
});

test("record activity scopes the shared run collection to canonical model and public identity", async () => {
  const onRequest = vi.fn();
  render(<CatalogueStory record onRequest={onRequest} />);
  await screen.findByText("Failed");
  const request = onRequest.mock.calls.find(([entry]) => entry.query.includes("workflowrun("))?.[0];
  expect(request?.variables.where).toEqual({ _and: [
    { status: { _eq: "failed" } },
    { subject_id: { _eq: "nte_7" } }, { subject_model: { _eq: "notes.Note" } },
  ] });
});

test("unreadable workflows show an empty state without a retry action", async () => {
  render(Unavailable.render());
  expect(await screen.findByRole("heading", { name: "Record not found" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
});

test("transport errors offer the shared reload action", async () => {
  render(QueryError.render());
  await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy(), { timeout: 10000 });
  expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  expect(screen.queryByText("Record not found")).toBeNull();
}, 15000);
