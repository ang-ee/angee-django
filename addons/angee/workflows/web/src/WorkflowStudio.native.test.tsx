// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, expect, test, vi } from "vitest";
import { StudioStory } from "./WorkflowStudio.stories";

beforeAll(() => {
  class ResizeObserverStub { observe(): void {} unobserve(): void {} disconnect(): void {} }
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
});
afterEach(cleanup);

async function selectEntry() {
  const node = await screen.findByTestId("rf__node-entry");
  fireEvent.click(node);
  return screen.findByRole("textbox", { name: "Key" });
}

test("generated inspector edits, undo, save, and publication use the native owners", async () => {
  const requests = vi.fn();
  render(<StudioStory onRequest={requests} />);
  const key = await selectEntry();
  expect(await screen.findByText("Selected record")).toBeTruthy();
  fireEvent.focus(key);
  fireEvent.change(key, { target: { value: "renamed" } });
  fireEvent.blur(key);
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  await waitFor(() => expect((key as HTMLInputElement).value).toBe("entry"));
  fireEvent.click(screen.getByRole("button", { name: "Redo" }));
  await waitFor(() => expect((key as HTMLInputElement).value).toBe("renamed"));
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Save draft" }).hasAttribute("disabled")).toBe(true));
  const save = requests.mock.calls.map(([request]) => request).find((request) => request.query.includes("save_workflow_draft"));
  expect(save.variables).toMatchObject({ revision: 1, layout: { renamed: [80, 60] }, draft: {
    nodes: { renamed: { config: { target: "nte_7", threshold: 1 } } }, results: [{ from: "renamed" }],
  } });
  fireEvent.click(screen.getByRole("button", { name: "Publish" }));
  expect(await screen.findByText("Published version 2")).toBeTruthy();
  expect(await screen.findByText(/record_parent/)).toBeTruthy();
});

test("conflicts keep edits and overwrite submits the observed revision", async () => {
  const requests = vi.fn();
  render(<StudioStory conflict onRequest={requests} />);
  const key = await selectEntry();
  fireEvent.change(key, { target: { value: "mine" } });
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  fireEvent.click(await screen.findByRole("button", { name: "Overwrite with mine" }));
  await waitFor(() => expect(requests.mock.calls.map(([request]) => request).filter((request) => request.query.includes("save_workflow_draft"))).toHaveLength(2));
  const saves = requests.mock.calls.map(([request]) => request).filter((request) => request.query.includes("save_workflow_draft"));
  expect(saves.map((request) => request.variables.revision)).toEqual([1, 2]);
  expect(saves[1].variables.draft.nodes.mine).toBeTruthy();
});

test("discard reloads the server draft and clears the conflict", async () => {
  render(<StudioStory conflict />);
  const key = await selectEntry();
  fireEvent.change(key, { target: { value: "mine" } });
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  fireEvent.click(await screen.findByRole("button", { name: "Discard mine and load latest" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Overwrite with mine" })).toBeNull());
  fireEvent.click(await screen.findByTestId("rf__node-entry"));
  expect((await screen.findByRole("textbox", { name: "Key" }) as HTMLInputElement).value).toBe("entry");
});

test("a failed latest read preserves edits and keeps conflict recovery available", async () => {
  render(<StudioStory conflict failLatest />);
  const key = await selectEntry();
  fireEvent.change(key, { target: { value: "mine" } });
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  fireEvent.click(await screen.findByRole("button", { name: "Discard mine and load latest" }));
  expect(await screen.findByText("Request failed.", {}, { timeout: 10000 })).toBeTruthy();
  expect((screen.getByRole("textbox", { name: "Key" }) as HTMLInputElement).value).toBe("mine");
  expect(screen.getByRole("button", { name: "Overwrite with mine" }).hasAttribute("disabled")).toBe(false);
}, 15000);

test("publication issues locate the generated config field and flag its node", async () => {
  render(<StudioStory invalid />);
  await selectEntry();
  fireEvent.click(screen.getByRole("button", { name: "Publish" }));
  expect(await screen.findByText("Threshold is too small.")).toBeTruthy();
  expect(await screen.findByText("Issues")).toBeTruthy();
});

test("palette excludes internal steps and structural edits persist layout", async () => {
  const requests = vi.fn();
  render(<StudioStory onRequest={requests} />);
  await screen.findByTestId("rf__node-entry");
  fireEvent.click(screen.getByRole("button", { name: "Add step" }));
  expect(screen.queryByRole("button", { name: "Internal" })).toBeNull();
  fireEvent.click(await screen.findByRole("button", { name: "Echo" }));
  const key = await screen.findByRole("textbox", { name: "Key" });
  expect((key as HTMLInputElement).value).toBe("echo");
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  await waitFor(() => expect(requests.mock.calls.some(([request]) => request.query.includes("save_workflow_draft"))).toBe(true));
  const save = requests.mock.calls.map(([request]) => request).find((request) => request.query.includes("save_workflow_draft"));
  expect(save.variables).toMatchObject({ layout: { entry: [80, 60], echo: [440, 80] }, draft: { nodes: { echo: { step: "echo" } } } });
});

test("adding from a port, inserting, disconnecting, deleting and moving compose GraphEditor", async () => {
  const requests = vi.fn();
  render(<StudioStory onRequest={requests} />);
  await selectEntry();
  fireEvent.click(await screen.findByRole("button", { name: "Add from Done on Entry" }));
  fireEvent.click(await screen.findByRole("button", { name: "Echo" }));
  fireEvent.click(await screen.findByRole("button", { name: "Insert on Entry, Done, to Echo" }));
  fireEvent.click(await screen.findByRole("button", { name: "Echo" }));
  expect((await screen.findByRole("textbox", { name: "Key" }) as HTMLInputElement).value).toBe("echo_2");
  fireEvent.click(await screen.findByRole("button", { name: "Delete link from Entry, Done, to Echo" }));
  fireEvent.click(screen.getByRole("button", { name: "Delete Echo" }));
  const entry = screen.getByTestId("rf__node-entry");
  fireEvent.click(entry);
  fireEvent.keyDown(entry, { key: "ArrowRight" });
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  await waitFor(() => expect(requests.mock.calls.some(([request]) => request.query.includes("save_workflow_draft"))).toBe(true));
  const save = requests.mock.calls.map(([request]) => request).find((request) => request.query.includes("save_workflow_draft"));
  expect(save.variables.draft.nodes.entry.next).toEqual({});
  expect(save.variables.draft.nodes.echo_2).toBeUndefined();
  expect(save.variables.draft.nodes.echo).toBeTruthy();
  expect(save.variables.layout.entry).toEqual([85, 60]);
});

test("a declared config relation uses the native picker and submits its record identity", async () => {
  const requests = vi.fn();
  render(<StudioStory onRequest={requests} />);
  await selectEntry();
  fireEvent.click(await screen.findByRole("button", { name: "Target record: Selected record" }));
  fireEvent.click(await screen.findByRole("option", { name: "Another record" }));
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  await waitFor(() => expect(requests.mock.calls.some(([request]) => request.query.includes("save_workflow_draft"))).toBe(true));
  const save = requests.mock.calls.map(([request]) => request).find((request) => request.query.includes("save_workflow_draft"));
  expect(save.variables.draft.nodes.entry.config.target).toBe("nte_8");
});
