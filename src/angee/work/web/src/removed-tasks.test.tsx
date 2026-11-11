// @vitest-environment happy-dom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { RuntimeFixture, storySchema, testDataResource, type GraphQLWireRequest } from "@angee/storybook/testing";

import { RemovedTasks } from "./removed-tasks";

const removed = {
  id: "tsk_q", title: "Can this ship today?", revision: 4, parent: "tsk_req",
  removed_at: "2026-10-01T00:00:00Z", removed_by_label: "Ada", removal_reason: "Answered elsewhere",
};

function renderRemoved(content: ReactNode, rows: readonly Record<string, unknown>[]): GraphQLWireRequest[] {
  const requests: GraphQLWireRequest[] = [];
  const schemas = { console: { ...storySchema({
    removed_tasks: () => rows,
    restore_task: (args) => ({ ok: true, message: "Task restored.", id: args.task, validation_errors: null }),
  }, (request) => requests.push(request)).public!, metadata: { angee: { resources: [testDataResource("projects.Task")] } } } };
  render(<RuntimeFixture activeSchema="console" schemas={schemas}>{content}</RuntimeFixture>);
  return requests;
}

test("lists a parent's removed tasks and restores one at its revision", async () => {
  const requests = renderRemoved(<RemovedTasks parent="tsk_req" />, [removed]);
  fireEvent.click(await screen.findByRole("button", { name: "Removed (1)" }));
  expect(requests.find(({ fields }) => fields.includes("removed_tasks"))?.variables).toEqual({ queue: null, parent: "tsk_req" });
  expect(screen.getByText("Can this ship today?")).toBeTruthy();
  expect(screen.getByText("Reason: Answered elsewhere")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Restore" }));
  await waitFor(() => expect(requests.find(({ fields }) => fields.includes("restore_task"))?.variables)
    .toEqual({ task: "tsk_q", expected_revision: 4 }));
  expect(await screen.findByText("Task restored.")).toBeTruthy();
});

test("renders nothing for a reader the server lists no removed tasks for", async () => {
  const requests = renderRemoved(<RemovedTasks queue="que_eng" />, []);
  await waitFor(() => expect(requests.some(({ fields }) => fields.includes("removed_tasks"))).toBe(true));
  expect(screen.queryByRole("button", { name: /Removed/ })).toBeNull();
});
