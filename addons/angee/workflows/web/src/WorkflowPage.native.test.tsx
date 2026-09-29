// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, expect, test, vi } from "vitest";

import { Catalogue, CatalogueStory, QueryError, Unavailable } from "./WorkflowPage.stories";

beforeAll(() => { Element.prototype.getAnimations ??= () => []; });
afterEach(cleanup);

test("opens a workflow from the catalogue with its retained versions and recent runs", async () => {
  render(Catalogue.render());
  const workflow = await screen.findByRole("link", { name: /record_review/ });
  expect(workflow.getAttribute("href")).toBe("/workflows/wfl_review");
  expect(screen.queryByRole("button", { name: /New/ })).toBeNull();
  fireEvent.click(workflow);
  expect(await screen.findByRole("heading", { level: 1, name: "Record review" })).toBeTruthy();
  expect(screen.getByRole("heading", { level: 2, name: "Versions" })).toBeTruthy();
  expect(screen.getByRole("heading", { level: 2, name: "Recent runs" })).toBeTruthy();
  expect(await screen.findByText("retained_hash")).toBeTruthy();
  expect(await screen.findByRole("link", { name: /Failed/i })).toBeTruthy();
  expect(screen.getByText("A retained review process.")).toBeTruthy();
});

test("versions and recent runs are filtered by the open workflow using native list transport", async () => {
  const onRequest = vi.fn();
  render(<CatalogueStory onRequest={onRequest} />);
  await screen.findByText("retained_hash");
  await screen.findByRole("link", { name: /Failed/i });
  for (const name of ["workflowversion", "workflowrun"]) {
    const request = onRequest.mock.calls.find(([entry]) => entry.query.includes(`${name}(`))?.[0];
    expect(request?.variables.where).toEqual({ _and: [{ workflow: { _eq: "wfl_review" } }] });
  }
});

test("record activity scopes the shared run collection to canonical model and public identity", async () => {
  const onRequest = vi.fn();
  render(<CatalogueStory record onRequest={onRequest} />);
  await screen.findByRole("link", { name: /Failed/i });
  const request = onRequest.mock.calls.find(([entry]) => entry.query.includes("workflowrun("))?.[0];
  expect(request?.variables.where).toEqual({ _and: [
    { subject_id: { _eq: "nte_7" } }, { subject_model: { _eq: "notes.Note" } },
  ] });
});

test("unreadable workflows show an empty state without a retry action", async () => {
  render(Unavailable.render());
  expect(await screen.findByText("This workflow was not found or is not readable.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Reload" })).toBeNull();
});

test("transport errors offer the shared reload action", async () => {
  render(QueryError.render());
  await waitFor(() => expect(screen.getByRole("button", { name: "Reload" })).toBeTruthy(), { timeout: 10000 });
  expect(screen.getByText("Could not load this workflow.")).toBeTruthy();
  expect(screen.queryByText("This workflow was not found or is not readable.")).toBeNull();
}, 15000);
