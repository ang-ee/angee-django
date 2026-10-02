// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, expect, test, vi } from "vitest";
import { StudioStory } from "./WorkflowStudio.stories";
const guards = vi.hoisted(() => ({ callbacks: [] as unknown[] }));
vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return { ...actual, useUnsavedChangesNavigationGuard: (options: Parameters<typeof actual.useUnsavedChangesNavigationGuard>[0]) => {
    guards.callbacks.push(options.isDirtyNow);
    return actual.useUnsavedChangesNavigationGuard(options);
  } };
});

beforeAll(() => {
  class ResizeObserverStub { observe(): void {} unobserve(): void {} disconnect(): void {} }
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
});
afterEach(() => { cleanup(); guards.callbacks = []; });

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
  expect(save.variables).toMatchObject({ revision: 1, nodeKeys: { entry: "renamed" }, layout: { entry: [80, 60] }, draft: {
    nodes: { entry: { config: { target: "nte_7", threshold: 1 } } }, results: [{ from: "entry" }],
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
  expect(await screen.findAllByText("Draft changed since it was loaded.")).toHaveLength(1);
  fireEvent.click(await screen.findByRole("button", { name: "Overwrite with mine" }));
  await waitFor(() => expect(requests.mock.calls.map(([request]) => request).filter((request) => request.query.includes("save_workflow_draft"))).toHaveLength(2));
  const saves = requests.mock.calls.map(([request]) => request).filter((request) => request.query.includes("save_workflow_draft"));
  expect(saves.map((request) => request.variables.revision)).toEqual([1, 2]);
  expect(saves[1].variables.nodeKeys.entry).toBeTruthy();
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
  fireEvent.change(key, { target: { value: "still_mine" } });
  expect(screen.getByText("Request failed.")).toBeTruthy();
}, 15000);

test("publication issues locate the generated config field and flag its node", async () => {
  render(<StudioStory invalid />);
  await selectEntry();
  fireEvent.click(screen.getByRole("button", { name: "Publish" }));
  expect(await screen.findByText("Threshold is too small.")).toBeTruthy();
  expect(await screen.findByText("Issues")).toBeTruthy();
  expect(screen.getByText("Draft could not be published. Resolve the highlighted issues.")).toBeTruthy();
  const threshold = screen.getByRole("textbox", { name: "Threshold" });
  fireEvent.change(threshold, { target: { value: "2" } });
  await waitFor(() => expect(screen.queryByText("Threshold is too small.")).toBeNull());
  fireEvent.change(screen.getByRole("textbox", { name: "Label" }), { target: { value: "Changed label" } });
  expect(screen.queryByText("Threshold is too small.")).toBeNull();
});

test.each([[true, false, "Draft could not be saved. Resolve the highlighted issues."],
  [false, true, "Draft saved. Resolve the highlighted issues before publishing."]] as const)(
  "save issues distinguish refusal from acknowledgement (%s, %s)", async (saveInvalid, savedDiagnostics, message) => {
    render(<StudioStory saveInvalid={saveInvalid} savedDiagnostics={savedDiagnostics} />);
    const key = await selectEntry();
    fireEvent.change(key, { target: { value: "entry" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Label" }), { target: { value: "Edited" } });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    expect(await screen.findByText(message)).toBeTruthy();
    expect(screen.getByText("Threshold is too small.")).toBeTruthy();
    if (saveInvalid) expect(screen.queryByText(/Draft saved\./)).toBeNull();
  });

test("an unused invalid FormSpec cannot break the Studio or its working inspector", async () => {
  render(<StudioStory badUnusedSchema />);
  await selectEntry();
  expect(screen.getByRole("textbox", { name: "Threshold" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Save draft" })).toBeTruthy();
});

test("editing keeps the navigation blocker's live dirtiness callback stable", async () => {
  render(<StudioStory />);
  const key = await selectEntry();
  fireEvent.change(key, { target: { value: "first_edit" } });
  fireEvent.change(key, { target: { value: "second_edit" } });
  expect(guards.callbacks.length).toBeGreaterThan(1);
  expect(new Set(guards.callbacks).size).toBe(1);
});

test("local duplicate-key refusal uses its own message and never acknowledges a save", async () => {
  const requests = vi.fn();
  render(<StudioStory linked onRequest={requests} />);
  await screen.findByTestId("rf__node-entry");
  fireEvent.click(screen.getByTestId("rf__node-second"));
  fireEvent.change(await screen.findByRole("textbox", { name: "Key" }), { target: { value: "entry" } });
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  expect(await screen.findByText("Draft could not be saved. Node keys must be unique.")).toBeTruthy();
  expect(screen.queryByText(/Draft saved\./)).toBeNull();
  expect(requests.mock.calls.some(([request]) => request.query.includes("save_workflow_draft"))).toBe(false);
});

test("configured outcome issues are visible and flag their node", async () => {
  render(<StudioStory outcomeIssue />);
  expect(await screen.findByText("entry: This step's configured outcome is invalid.")).toBeTruthy();
  expect(await screen.findByText("Issues")).toBeTruthy();
});

test("an empty persisted layout displays automatic horizontal positions without a dirty draft, and saves them on explicit save", async () => {
  const requests = vi.fn();
  render(<StudioStory linked emptyLayout onRequest={requests} />);
  await selectEntry();
  expect(screen.getByRole("button", { name: "Save draft" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByTestId("rf__node-entry").style.transform).not.toBe(screen.getByTestId("rf__node-second").style.transform);
  fireEvent.change(screen.getByRole("textbox", { name: "Label" }), { target: { value: "Edited" } });
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  await waitFor(() => expect(requests.mock.calls.some(([request]) => request.query.includes("save_workflow_draft"))).toBe(true));
  const save = requests.mock.calls.map(([request]) => request).find((request) => request.query.includes("save_workflow_draft"));
  expect(Object.keys(save.variables.layout).sort()).toEqual(["entry", "second"]);
  expect(save.variables.layout.second[0]).toBeGreaterThan(save.variables.layout.entry[0]);
  expect(save.variables.layout.second[1]).toBe(save.variables.layout.entry[1]);
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
  const created = Object.entries(save.variables.nodeKeys).find(([, key]) => key === "echo")?.[0];
  expect(created).toBeTruthy();
  expect(save.variables.layout[created!]).toEqual([440, 80]);
  expect(save.variables.draft.nodes[created!].step).toBe("echo");
});

test("adding from a port, inserting, disconnecting, deleting and moving compose GraphEditor", async () => {
  const requests = vi.fn();
  render(<StudioStory onRequest={requests} />);
  await selectEntry();
  fireEvent.click(await screen.findByRole("button", { name: "Add from Done on Entry (entry)" }));
  fireEvent.click(await screen.findByRole("button", { name: "Echo" }));
  fireEvent.click(await screen.findByRole("button", { name: "Insert on Entry (entry), Done, to Echo (echo)" }));
  fireEvent.click(await screen.findByRole("button", { name: "Echo" }));
  expect((await screen.findByRole("textbox", { name: "Key" }) as HTMLInputElement).value).toBe("echo_2");
  fireEvent.click(await screen.findByRole("button", { name: "Delete link from Entry (entry), Done, to Echo (echo_2)" }));
  fireEvent.click(screen.getByRole("button", { name: "Delete Echo (echo_2)" }));
  const entry = screen.getByTestId("rf__node-entry");
  fireEvent.click(entry);
  fireEvent.keyDown(entry, { key: "ArrowRight" });
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  await waitFor(() => expect(requests.mock.calls.some(([request]) => request.query.includes("save_workflow_draft"))).toBe(true));
  const save = requests.mock.calls.map(([request]) => request).find((request) => request.query.includes("save_workflow_draft"));
  expect(save.variables.draft.nodes.entry.next).toEqual({});
  expect(Object.values(save.variables.nodeKeys)).toEqual(["entry", "echo"]);
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

test("monitor-only Studio displays the draft and inspector without editing controls", async () => {
  render(<StudioStory readOnly />);
  fireEvent.click(await screen.findByTestId("rf__node-entry"));
  expect(await screen.findByText("Selected record")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Add step" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "Publish" }).hasAttribute("disabled")).toBe(true);
  expect(screen.queryByRole("textbox", { name: "Key" })).toBeNull();
});

test("typing config keeps graph outcomes and editing available until the history group commits", async () => {
  let release: (() => void) | undefined;
  let reads = 0;
  const gate = () => ++reads === 1 ? Promise.resolve() : new Promise<void>((resolve) => { release = resolve; });
  const requests = vi.fn();
  render(<StudioStory outcomesGate={gate} onRequest={requests} />);
  await selectEntry();
  const threshold = screen.getByRole("textbox", { name: "Threshold" });
  await screen.findByRole("button", { name: "Add from Done on Entry (entry)" });
  const count = requests.mock.calls.filter(([request]) => request.query.includes("workflow_step_outcomes")).length;
  fireEvent.focus(threshold);
  fireEvent.change(threshold, { target: { value: "12" } });
  fireEvent.change(threshold, { target: { value: "123" } });
  expect(requests.mock.calls.filter(([request]) => request.query.includes("workflow_step_outcomes"))).toHaveLength(count);
  fireEvent.blur(threshold);
  await waitFor(() => expect(requests.mock.calls.filter(([request]) => request.query.includes("workflow_step_outcomes"))).toHaveLength(count + 1));
  expect(screen.getByRole("button", { name: "Add from Done on Entry (entry)" }).hasAttribute("disabled")).toBe(false);
  expect(screen.getByRole("button", { name: "Delete Entry (entry)" }).hasAttribute("disabled")).toBe(false);
  release?.();
});

test("undo and redo cannot erase conflict recovery controls", async () => {
  render(<StudioStory conflict />);
  const key = await selectEntry();
  fireEvent.focus(key);
  fireEvent.change(key, { target: { value: "mine" } });
  fireEvent.blur(key);
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  await screen.findByRole("button", { name: "Overwrite with mine" });
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  expect(screen.getByRole("button", { name: "Overwrite with mine" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Redo" }));
  expect(screen.getByRole("button", { name: "Discard mine and load latest" })).toBeTruthy();
});

test("new nodes have distinct accessible keys and never reuse a deleted node key or identity", async () => {
  const requests = vi.fn();
  render(<StudioStory onRequest={requests} />);
  await selectEntry();
  const add = async () => {
    fireEvent.click(screen.getByRole("button", { name: "Add step" }));
    fireEvent.click(await screen.findByRole("button", { name: "Echo" }));
  };
  await add();
  await screen.findByRole("button", { name: "Select Echo (echo)" });
  fireEvent.click(await screen.findByRole("button", { name: "Delete Echo (echo)" }));
  await add();
  await screen.findByRole("button", { name: "Select Echo (echo_2)" });
  const ids = requests.mock.calls.filter(([request]) => request.query.includes("workflow_step_outcomes"))
    .flatMap(([request]) => request.variables.configurations).filter((entry) => entry.node !== "entry").map((entry) => entry.node);
  expect(new Set(ids).size).toBe(2);
  expect(screen.queryByRole("button", { name: "Select Echo (echo)" })).toBeNull();
});

test("a hidden retained Studio keeps its navigation guard and selection opens the inspector pane", async () => {
  render(<StudioStory retained />);
  fireEvent.click(await screen.findByRole("button", { name: "Collapse inspector" }));
  const key = await selectEntry();
  expect(screen.getByTestId("pane-state").textContent).toBe("false:workflow-inspector");
  fireEvent.change(key, { target: { value: "retained" } });
  fireEvent.click(screen.getByRole("button", { name: "Hide studio" }));
  expect(screen.queryByRole("textbox", { name: "Key" })).toBeNull();
  fireEvent.click(screen.getByRole("link", { name: "Leave studio" }));
  expect(await screen.findByRole("alertdialog", { name: "Unsaved changes - leave without saving?" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Stay" }));
  fireEvent.click(screen.getByRole("button", { name: "Show studio" }));
  expect((await screen.findByRole("textbox", { name: "Key" }) as HTMLInputElement).value).toBe("retained");
  expect(screen.getByTestId("workflow-studio-canvas").className).toContain("h-[65vh]");
});

test("links wait for their declared source handles on the first outcomes load", async () => {
  let release: (() => void) | undefined;
  const gate = () => new Promise<void>((resolve) => { release = resolve; });
  render(<StudioStory linked outcomesGate={gate} />);
  await screen.findByTestId("rf__node-entry");
  await waitFor(() => expect(release).toBeTypeOf("function"));
  expect(screen.queryByRole("button", { name: "Delete link from Entry (entry), Done, to Second (second)" })).toBeNull();
  await act(async () => { release?.(); });
  expect(await screen.findByRole("button", { name: "Delete link from Entry (entry), Done, to Second (second)" })).toBeTruthy();
});

test("adding a client identity retains issues on existing nodes", async () => {
  render(<StudioStory invalid />);
  await selectEntry();
  fireEvent.click(screen.getByRole("button", { name: "Publish" }));
  await screen.findByText("Threshold is too small.");
  fireEvent.click(screen.getByRole("button", { name: "Add step" }));
  fireEvent.click(await screen.findByRole("button", { name: "Echo" }));
  await screen.findByRole("button", { name: "Select Echo (echo)" });
  fireEvent.click(screen.getByTestId("rf__node-entry"));
  await screen.findByRole("textbox", { name: "Key" });
  expect(screen.getByText("Threshold is too small.")).toBeTruthy();
  expect(screen.getByText("Issues")).toBeTruthy();
});

test("unrendered document and config issues appear once in the node-prefixed summary", async () => {
  render(<StudioStory unrenderedIssues />);
  await selectEntry();
  fireEvent.click(screen.getByRole("button", { name: "Publish" }));
  const summary = await screen.findByText(/entry: Missing binding\. entry: Unknown config field\./);
  expect(summary).toBeTruthy();
  expect(screen.getAllByText(/Missing binding\./)).toHaveLength(1);
});

test("config issues are reapplied after history resets the form", async () => {
  render(<StudioStory invalid />);
  await selectEntry();
  const threshold = screen.getByRole("textbox", { name: "Threshold" });
  fireEvent.focus(threshold);
  fireEvent.change(threshold, { target: { value: "2" } });
  fireEvent.blur(threshold);
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Publish" }).hasAttribute("disabled")).toBe(false));
  fireEvent.focus(threshold);
  fireEvent.change(threshold, { target: { value: "3" } });
  fireEvent.blur(threshold);
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  fireEvent.click(screen.getByRole("button", { name: "Publish" }));
  await screen.findByText("Threshold is too small.");
  fireEvent.click(screen.getByRole("button", { name: "Redo" }));
  expect(screen.getByText("Threshold is too small.")).toBeTruthy();
});
