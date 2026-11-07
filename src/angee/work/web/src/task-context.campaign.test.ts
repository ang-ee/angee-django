import { expect, test, vi } from "vitest";

const query = vi.hoisted(() => vi.fn(() => ({ data: null })));
vi.mock("@angee/projects", () => ({ TASK_MODEL: "projects.Task" }));
vi.mock("@angee/refine", () => ({ useAuthoredQuery: query }));
vi.mock("./documents", () => ({
  WorkQueueContextDocument: {}, WorkTaskContextDocument: { kind: "Document" }, WorkCycleContextDocument: {},
}));

import { useTaskContext } from "./context";
import { WorkTaskContextDocument } from "./documents";

test("task category subscribes to Task and Stage invalidation through the shared query owner", () => {
  useTaskContext("tsk_context");
  expect(query).toHaveBeenLastCalledWith(WorkTaskContextDocument, { id: "tsk_context" }, {
    enabled: true, models: ["projects.Task", "work.Stage"],
  });
  useTaskContext("");
  expect(query).toHaveBeenLastCalledWith(WorkTaskContextDocument, { id: "" }, {
    enabled: false, models: ["projects.Task", "work.Stage"],
  });
});
