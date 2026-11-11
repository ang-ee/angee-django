// @vitest-environment happy-dom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";
import { operationDocuments } from "@angee/gql/console/actions";
import { RecordChromeProvider } from "@angee/ui";
import { RuntimeFixture, storySchema, testDataResource, type StoryOperation, type StoryRequest } from "@angee/storybook/testing";

import { TriageRecordActions } from "./triage-actions";
import type { WorkTaskRow } from "./task-work";

type Stage = { category: string } | null;

const resources = [
  testDataResource("projects.Task", { roots: { list: "project_tasks", detail: "project_tasks_by_pk", aggregate: "project_tasks_aggregate" } }),
  testDataResource("work.Stage", { roots: { list: "work_stages", detail: "work_stages_by_pk" } }),
];

const queued = (overrides: Partial<WorkTaskRow> = {}): WorkTaskRow => ({
  id: "tsk_toolbar", queue: { id: "que_work" }, stage: { category: "UNSTARTED" }, task_actions: ["start", "return_to_triage"], ...overrides,
});

/** The record toolbar's verbs over the generated action documents; `stage` is the task's authored category read. */
function renderActions({ record, stage = null, readOnly = false, start }: {
  record: WorkTaskRow | null; stage?: Stage; readOnly?: boolean; start?: StoryOperation;
}): StoryRequest[] {
  const requests: StoryRequest[] = [];
  const schemas = { console: { ...storySchema({
    project_tasks_by_pk: (args) => ({ id: args.id, stage }),
    start_task: start ?? ((args) => ({ ok: true, message: "Started.", id: args.id, validation_errors: null })),
    return_task_to_triage: (args) => ({ ok: true, message: "Returned to triage.", id: args.id, validation_errors: null }),
  }, (request) => requests.push(request)).public!, metadata: { angee: { resources } } } };
  render(<RuntimeFixture activeSchema="console" schemas={schemas} operationDocuments={{ console: operationDocuments }}>
    <RecordChromeProvider value={{ resource: "projects.Task", canonicalResource: "projects.Task", dataProviderName: "console",
      recordId: "tsk_toolbar", record, formReadOnly: readOnly }}>
      <TriageRecordActions />
    </RecordChromeProvider>
  </RuntimeFixture>);
  return requests;
}

async function openVerbs(): Promise<void> {
  fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
}

test.each(["BACKLOG", "unstarted"])("a %s task offers Start and Return to triage, each sent for the open record", async (category) => {
  const requests = renderActions({ record: queued({ stage: { category } }), stage: { category } });
  await openVerbs();
  fireEvent.click(await screen.findByRole("menuitem", { name: "Start" }));
  expect(await screen.findByText("Started.")).toBeTruthy();
  expect(requests.find(({ fields }) => fields.includes("start_task"))?.variables).toMatchObject({ id: "tsk_toolbar" });
  await openVerbs();
  fireEvent.click(await screen.findByRole("menuitem", { name: "Return to triage" }));
  expect(await screen.findByText("Returned to triage.")).toBeTruthy();
  expect(requests.find(({ fields }) => fields.includes("return_task_to_triage"))?.variables).toMatchObject({ id: "tsk_toolbar" });
});

test.each(["STARTED", "COMPLETED"])("a %s task offers only Return to triage", async (category) => {
  renderActions({ record: queued({ stage: { category } }), stage: { category } });
  await openVerbs();
  expect(await screen.findByRole("menuitem", { name: "Return to triage" })).toBeTruthy();
  expect(screen.queryByRole("menuitem", { name: "Start" })).toBeNull();
});

test.each([
  ["no record", { record: null }],
  ["no queue", { record: queued({ queue: null }) }],
  ["a read-only form", { record: queued(), readOnly: true }],
  ["a task its owner admits no hand verb for", { record: queued({ task_actions: [] }) }],
])("%s shows no verbs", async (_case, options) => {
  const requests = renderActions(options);
  if (options.record) await waitFor(() => expect(requests.some(({ fields }) => fields.includes("project_tasks_by_pk"))).toBe(true));
  expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
});

test("Return follows the row's task actions, never a client queue read", async () => {
  renderActions({ record: queued({ task_actions: ["start"] }), stage: { category: "UNSTARTED" } });
  await openVerbs();
  expect(await screen.findByRole("menuitem", { name: "Start" })).toBeTruthy();
  expect(screen.queryByRole("menuitem", { name: "Return to triage" })).toBeNull();
});

test("Start waits until the task's stage category is known", async () => {
  renderActions({ record: queued({ stage: null }) });
  await openVerbs();
  expect(await screen.findByRole("menuitem", { name: "Return to triage" })).toBeTruthy();
  expect(screen.queryByRole("menuitem", { name: "Start" })).toBeNull();
});

test("the form's waiting category yields to the task's authored category once it loads", async () => {
  const requests = renderActions({ record: queued(), stage: { category: "STARTED" } });
  await waitFor(() => expect(requests.some(({ fields }) => fields.includes("project_tasks_by_pk"))).toBe(true));
  await openVerbs();
  expect(await screen.findByRole("menuitem", { name: "Return to triage" })).toBeTruthy();
  await waitFor(() => expect(screen.queryByRole("menuitem", { name: "Start" })).toBeNull());
});

test("while Start is pending the verbs wait and neither fires again", async () => {
  let settle!: (value: unknown) => void;
  const requests = renderActions({ record: queued(), stage: { category: "UNSTARTED" },
    start: () => new Promise((resolve) => { settle = resolve; }) });
  await openVerbs();
  fireEvent.click(await screen.findByRole("menuitem", { name: "Start" }));
  await waitFor(() => expect(requests.filter(({ fields }) => fields.includes("start_task"))).toHaveLength(1));
  const trigger = screen.getByRole("button", { name: "Actions" });
  await waitFor(() => expect(trigger.getAttribute("aria-busy")).toBe("true"));
  fireEvent.click(trigger);
  expect(screen.queryByRole("menuitem", { name: "Return to triage" })).toBeNull();
  settle({ ok: true, message: "Started.", id: "tsk_toolbar", validation_errors: null });
  expect(await screen.findByText("Started.")).toBeTruthy();
  expect(requests.filter(({ fields }) => fields.includes("start_task"))).toHaveLength(1);
  expect(requests.some(({ fields }) => fields.includes("return_task_to_triage"))).toBe(false);
});

test("a task in triage keeps the four triage verbs", async () => {
  renderActions({ record: queued({ started_triage_at: "2026-01-01", triaged_at: null, stage: { category: "TRIAGE" } }), stage: { category: "TRIAGE" } });
  await openVerbs();
  for (const verb of ["Accept", "Decline", "Snooze", "Mark duplicate"]) {
    expect(await screen.findByRole("menuitem", { name: verb })).toBeTruthy();
  }
  expect(screen.queryByRole("menuitem", { name: "Start" })).toBeNull();
});
