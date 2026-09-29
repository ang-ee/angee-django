// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, expect, test } from "vitest";

import { Recovery, Waiting, RunStory, type RunRequest } from "./RunsPage.stories";
import { runFixture, stepRunFixture } from "./testing";

beforeAll(() => { Element.prototype.getAnimations ??= () => []; });
afterEach(cleanup);

async function action(label: string, scope: HTMLElement = document.body) {
  if (label === "Reprocess run") {
    fireEvent.click((await within(scope).findAllByRole("button", { name: "Actions" })).at(-1)!);
    fireEvent.click(await screen.findByRole("menuitem", { name: label }));
  } else {
    fireEvent.click(await within(scope).findByRole("button", { name: label }));
  }
  return screen.findByRole(label === "Retry accepting a possible duplicate" ? "dialog" : "alertdialog", { name: label });
}

async function openStep() {
  fireEvent.click(await screen.findByRole("tab", { name: "Step runs" }));
  fireEvent.click(await screen.findByRole("button", { name: "Open inspect" }));
  return screen.findByRole("dialog", { name: "Step Run" });
}

test("routed record uses the framework action menu, facts and retained JSON", async () => {
  render(Recovery.render());
  expect(await screen.findByText("Operator")).toBeTruthy();
  expect(await screen.findByText("Review notes")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Step runs" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Save|Delete|New/ })).toBeNull();
  expect(screen.queryByRole("button", { name: "Reprocess run" })).toBeNull();
  expect(screen.queryByText("Retained error")).toBeNull();
  await waitFor(() => expect(document.body.textContent).toContain("R-7"));
});

test("parent references and the child tab compose the existing scoped runs list", async () => {
  const requests: RunRequest[] = [];
  render(<RunStory run={runFixture({ parent_step: { id: "wsr_parent", run: { id: "wfr_parent" } } })}
    children={[runFixture({ id: "wfr_child", origin: "WORKFLOW" })]}
    onRequest={(request) => requests.push(request)} />);
  expect((await screen.findByRole("link", { name: "wfr_parent" })).getAttribute("href")).toBe("/workflows/runs/wfr_parent");
  fireEvent.click(await screen.findByRole("tab", { name: "Child runs" }));
  await waitFor(() => expect(requests.some(({ variables }) =>
    (JSON.stringify(variables.where) ?? "").includes('"parent_step__run":{"_eq":"wfr_review"}'))).toBe(true));
  await waitFor(() => expect(document.querySelector('a[href="/workflows/runs/wfr_child"]')).not.toBeNull());
});

test("a run waiter shows its target as a normal record reference", async () => {
  render(<RunStory steps={[stepRunFixture({ status: "WAITING", waiting_kind: "RUN",
    awaited_run: { id: "wfr_child" }, can_retry: false })]} />);
  const step = await openStep();
  expect(await within(step).findByText("Awaited run")).toBeTruthy();
  expect((await within(step).findByRole("link", { name: "wfr_child" })).getAttribute("href")).toBe("/workflows/runs/wfr_child");
});

test("trigger origin links to its retained admission event", async () => {
  render(<RunStory run={runFixture({ origin: "TRIGGER", trigger_event: { id: "wte_review" } })} />);
  expect(await screen.findByText("Trigger")).toBeTruthy();
  expect((await screen.findByRole("link", { name: "wte_review" })).getAttribute("href")).toBe("/workflows/trigger-events/wte_review");
});

test("reprocess confirms then navigates to the returned replacement through the route owner", async () => {
  const requests: RunRequest[] = [];
  render(<RunStory onRequest={(request) => requests.push(request)} />);
  const dialog = await action("Reprocess run");
  fireEvent.click(within(dialog).getByRole("button", { name: "Reprocess run" }));
  await waitFor(() => expect(requests.filter(({ query }) => query.includes("mutation"))).toHaveLength(1));
  expect(await screen.findByText("Run reprocessed.")).toBeTruthy();
  await waitFor(() => expect(requests.some(({ query, variables }) => query.includes("workflowrun_by_pk") && variables.id === "wfr_replacement")).toBe(true));
  expect(await screen.findByRole("heading", { name: "Run wfr_replacement" })).toBeTruthy();
});

test("cancel reports the backend outcome through the shared action lifecycle", async () => {
  render(Waiting.render());
  const dialog = await action("Cancel run");
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel run" }));
  expect(await screen.findByText("Open work canceled; the retained run is unchanged.")).toBeTruthy();
});

test("step rows are paged in execution order without per-row detail queries", async () => {
  const requests: RunRequest[] = [];
  const steps = Array.from({ length: 11 }, (_, index) => stepRunFixture({ id: `wsr_${index}`, node_key: `step-${index}`, rank: index }));
  render(<RunStory steps={steps} onRequest={(request) => requests.push(request)} />);
  fireEvent.click(await screen.findByRole("tab", { name: "Step runs" }));
  await screen.findByText("step-9");
  expect(screen.queryByText("step-10")).toBeNull();
  expect(requests.some(({ query }) => query.includes("steprun_by_pk"))).toBe(false);
  expect(requests.find(({ query }) => /\bsteprun\s*\(/.test(query))?.variables).toMatchObject({
    where: { _and: [{ run: { _eq: "wfr_review" } }] }, limit: 10, offset: 0, order_by: { rank: "asc", map_index: "asc" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  await screen.findByText("step-10");
  expect(requests.some(({ query }) => query.includes("steprun_by_pk"))).toBe(false);
});

test("map facts distinguish ordinary steps, item zero, and empty map parents", async () => {
  const steps = [
    stepRunFixture(),
    stepRunFixture({ id: "wsr_map", node_key: "reviews", rank: 1, is_map: true, map_total: 2, map_settled: 1 }),
    stepRunFixture({ id: "wsr_body", node_key: "reviews.body", rank: 1, is_mapped: true, map_index: 0 }),
    stepRunFixture({ id: "wsr_empty", node_key: "empty_reviews", rank: 2, is_map: true }),
  ];
  render(<RunStory steps={steps} />);
  fireEvent.click(await screen.findByRole("tab", { name: "Step runs" }));
  await screen.findByRole("button", { name: "Open empty_reviews" });
  const headers = screen.getAllByRole("columnheader");
  const indexes = ["Map index", "Map items settled", "Map items total"]
    .map((label) => headers.findIndex((header) => header.textContent === label));
  for (const [name, values] of [
    ["inspect", ["", "", ""]],
    ["reviews", ["", "1", "2"]],
    ["reviews.body", ["0", "", ""]],
    ["empty_reviews", ["", "0", "0"]],
  ] as const) {
    const row = screen.getByRole("button", { name: `Open ${name}` }).closest("tr")!;
    const cells = within(row).getAllByRole("cell");
    expect(indexes.map((index) => cells[index]?.textContent)).toEqual(values);
  }
  fireEvent.click(screen.getByRole("button", { name: "Open reviews.body" }));
  const body = await screen.findByRole("dialog", { name: "Step Run" });
  expect(await within(body).findByText("Map index")).toBeTruthy();
  expect(within(body).queryByText("Map items total")).toBeNull();
  fireEvent.click(within(body).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Step Run" })).toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "Open empty_reviews" }));
  const parent = await screen.findByRole("dialog", { name: "Step Run" });
  expect(await within(parent).findByText("Map items settled")).toBeTruthy();
  expect(within(parent).getByText("Map items total")).toBeTruthy();
  expect(within(parent).queryByText("Map index")).toBeNull();
});

test("selected step opens in the shared drawer with attempts and artifacts", async () => {
  render(Recovery.render());
  const step = await openStep();
  expect(await within(step).findByRole("heading", { name: "inspect" })).toBeTruthy();
  expect(within(step).queryByText("Wait reason")).toBeNull();
  expect(within(step).queryByText("Map index")).toBeNull();
  expect(within(step).queryByText("Map items settled")).toBeNull();
  expect(within(step).queryByText("Map items total")).toBeNull();
  fireEvent.click(within(step).getByRole("tab", { name: "Attempts" }));
  expect(await screen.findByText("The operation did not finish.")).toBeTruthy();
  fireEvent.click(await within(step).findByRole("button", { name: "Open 1" }));
  const attempt = await screen.findByRole("dialog", { name: "Step Attempt" });
  expect(await within(attempt).findByText("TimeoutError: operation expired")).toBeTruthy();
  fireEvent.click(within(attempt).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Step Attempt" })).toBeNull());
  fireEvent.click(within(step).getByRole("tab", { name: "Artifacts" }));
  expect((await screen.findByRole("link", { name: "Retained note" })).getAttribute("href")).toBe("/notes/nte_7");
});

test("run list groups and filters status, workflow and origin through shared metadata", async () => {
  const requests: RunRequest[] = [];
  render(<RunStory list onRequest={(request) => requests.push(request)} />);
  await screen.findByText("Failed");
  expect(requests.some(({ variables }) => JSON.stringify(variables.group_by) === '[{"field":"STATUS"}]')).toBe(true);
  fireEvent.click(await screen.findByLabelText("Filter and group"));
  fireEvent.click((await screen.findAllByRole("button", { name: "Failed" })).at(-1)!);
  fireEvent.click(screen.getByRole("button", { name: "Manual" }));
  await waitFor(() => expect(requests.some(({ variables }) => {
    const where = JSON.stringify(variables.where) ?? "";
    return where.includes('"status":{"_eq":"failed"}') && where.includes('"origin":{"_eq":"manual"}');
  })).toBe(true));
  fireEvent.click(await screen.findByRole("button", { name: "Record review" }));
  await waitFor(() => expect(requests.some(({ variables }) => (JSON.stringify(variables.where) ?? "").includes('"version__workflow":{"_eq":"wfl_review"}'))).toBe(true));
  const beforeWorkflowGroup = requests.length;
  fireEvent.click(screen.getByRole("button", { name: "Add custom group" }));
  fireEvent.click(screen.getByRole("combobox", { name: "Group field" }));
  const workflow = await screen.findByRole("option", { name: "Workflow" });
  fireEvent.pointerDown(workflow, { pointerType: "mouse" });
  fireEvent.click(workflow);
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Group field" }).textContent).toContain("Workflow"));
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  await waitFor(() => expect(requests.slice(beforeWorkflowGroup).some(({ variables }) => (JSON.stringify(variables.group_by) ?? "").includes("VERSION__WORKFLOW"))).toBe(true));
  const beforeOriginGroup = requests.length;
  fireEvent.click(screen.getByRole("button", { name: "Add custom group" }));
  fireEvent.click(screen.getByRole("combobox", { name: "Group field" }));
  const origin = await screen.findByRole("option", { name: "Origin" });
  fireEvent.pointerDown(origin, { pointerType: "mouse" });
  fireEvent.click(origin);
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Group field" }).textContent).toContain("Origin"));
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  fireEvent.click(screen.getByLabelText("Filter and group"));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Add custom group" })).toBeNull());
  fireEvent.click(await screen.findByRole("button", { name: "Record review" }));
  await waitFor(() => expect(requests.slice(beforeOriginGroup).some(({ variables }) => (JSON.stringify(variables.group_by) ?? "").includes("ORIGIN"))).toBe(true));
  await waitFor(() => expect(requests.some(({ variables }) => {
    const where = JSON.stringify(variables.where) ?? "";
    return where.includes('"status":{"_eq":"failed"}')
      && where.includes('"version__workflow":{"_eq":"wfl_review"}')
      && where.includes('"origin":{"_eq":"manual"}');
  })).toBe(true));
});

test("waiting reason appears only on a waiting step and retry uses backend capability", async () => {
  render(Waiting.render());
  const step = await openStep();
  expect(await within(step).findByText("Dispatch attempts exhausted.")).toBeTruthy();
  const dialog = await action("Retry step", step);
  fireEvent.click(within(dialog).getByRole("button", { name: "Retry step" }));
  expect(await screen.findByText("Step retried; the run is waiting.")).toBeTruthy();
});

test("duplicate retry requires a checked typed acknowledgement before any mutation", async () => {
  const requests: RunRequest[] = [];
  render(<RunStory steps={[stepRunFixture({ requires_duplicate_acknowledgement: true })]} onRequest={(request) => requests.push(request)} />);
  const step = await openStep();
  const label = "Retry accepting a possible duplicate";
  const dialog = await action(label, step);
  fireEvent.click(within(dialog).getByRole("button", { name: label }));
  const checkbox = within(dialog).getByRole("checkbox", { name: "I understand this retry may repeat an external effect." });
  await waitFor(() => expect(checkbox.getAttribute("aria-invalid")).toBe("true"));
  expect(requests.some(({ query }) => query.includes("mutation"))).toBe(false);
  fireEvent.click(checkbox);
  fireEvent.click(within(dialog).getByRole("button", { name: label }));
  expect(await screen.findByText("Step retried with duplicate risk acknowledged.")).toBeTruthy();
});

test("rejected actions show failure without navigation or success", async () => {
  render(<RunStory rejectAction />);
  const dialog = await action("Reprocess run");
  fireEvent.click(within(dialog).getByRole("button", { name: "Reprocess run" }));
  expect((await screen.findAllByText("This step cannot be retried in place.")).length).toBeGreaterThan(0);
  expect(screen.queryByText("Run reprocessed.")).toBeNull();
});

test("unavailable run details show the shared missing-record state", async () => {
  render(<RunStory unavailable />);
  expect(await screen.findByRole("heading", { name: "Record not found" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
});

test("run query failures retain the shared retry action", async () => {
  render(<RunStory queryError />);
  expect(await screen.findByRole("alert", undefined, { timeout: 10000 })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
}, 15000);

test("backend capability facts hide operator actions and retain complete error evidence", async () => {
  const error = "Retained details: " + "all evidence remains visible. ".repeat(50);
  render(<RunStory run={runFixture({ can_reprocess: false, error })} />);
  expect(await screen.findByText(error.trim())).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
});
