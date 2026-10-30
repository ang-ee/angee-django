import { expect, test } from "vitest";
import { createKeyedEntry, defaultWidgets, deserializeFormSpec } from "@angee/ui";
import { captureStudioIssues, dropStudioIssue, projectStudioIssues, studioSnapshot, studioValues } from "./studio-state";

const configFields = new Map([["echo", deserializeFormSpec({ type: "object", properties: {
  target: { type: "string" }, hidden: { type: "string", hidden: true },
} }, defaultWidgets)]]);

test("submits client-addressed declarations and one key map without interpreting binding grammar", () => {
  const values = studioValues({ nodes: {
    first: { step: "echo", input: { from: ["first", "input"] }, config: { source: "first" }, next: { done: "second" } },
    second: { step: "echo", body: { step: "echo", input: { from: "first" } } },
  }, results: [{ from: "second" }], outcome_labels: { done: "Complete" } }, { first: [10, 20], second: [30, 40] });
  values.entries[0]!.key = "renamed";
  const result = studioSnapshot(values);
  expect(result).toMatchObject({ status: "ok", data: { nodeKeys: { first: "renamed", second: "second" },
    draft: { nodes: { first: { input: { from: ["first", "input"] }, config: { source: "first" }, next: { done: "second" } },
      second: { body: { input: { from: "first" } } } }, results: [{ from: "second" }], outcome_labels: { done: "Complete" } },
    layout: { first: [10, 20], second: [30, 40] },
  } });
});

test("duplicate keys identify every client id while server errors follow reorder", () => {
  const values = studioValues({ nodes: { first: { step: "echo" }, second: { step: "echo" } } }, {});
  values.entries[1]!.key = "first";
  expect(studioSnapshot(values)).toMatchObject({ status: "invalid", issues: { fieldErrors: {
    first: ["Duplicate collection key: first"], second: ["Duplicate collection key: first"],
  } } });
  values.entries.reverse();
  const captured = captureStudioIssues({ fieldErrors: { "nodes.renamed.config.target": ["Missing"] }, formErrors: [] }, new Map([["renamed", "first"]]));
  expect(projectStudioIssues(captured, values.entries, (step) => configFields.get(step) ?? [])).toEqual({ fieldErrors: { "entries.1.value.config.target": ["Missing"] }, formErrors: [] });
});

test("captured issues stay on the same node after earlier deletion and unrendered declarations reach the summary", () => {
  const values = studioValues({ nodes: { first: { step: "echo" }, second: { step: "echo" } } }, {});
  const issues = captureStudioIssues({ fieldErrors: { "nodes.second.config.target": ["Missing"], "nodes.second.body.input": ["Bad binding"],
    "nodes.second.step": ["Retired implementation"], "nodes.second.config.hidden": ["Hidden value"],
    "nodes.second.config.unknown": ["Unknown config"] }, formErrors: [] }, new Map([["second", "second"]]));
  values.entries.shift();
  values.entries[0]!.key = "renamed";
  expect(projectStudioIssues(issues, values.entries, (step) => configFields.get(step) ?? [])).toEqual({ fieldErrors: { "entries.0.value.config.target": ["Missing"] },
    formErrors: ["renamed: Bad binding", "renamed: Retired implementation", "renamed: Hidden value", "renamed: Unknown config"] });
});

test("new client references remain opaque until Definition rekeys the submission", () => {
  const values = studioValues({ nodes: { first: { step: "echo" } }, results: [] }, {});
  const entry = createKeyedEntry({ step: "echo", label: "New", config: {}, next: {} }, "created");
  values.entries.push(entry);
  values.entries[0]!.value.next.done = [entry.clientId];
  values.document.results = [{ from: entry.clientId }];
  expect(studioSnapshot(values)).toMatchObject({ status: "ok", data: { nodeKeys: { [entry.clientId]: "created" }, draft: {
    nodes: { first: { next: { done: [entry.clientId] } } }, results: [{ from: entry.clientId }],
  } } });
});

test("invalid dotted authored keys still locate their client key control", () => {
  const values = studioValues({ nodes: { entry: { step: "echo" } } }, {});
  values.entries[0]!.key = "invalid.key";
  const captured = captureStudioIssues({ fieldErrors: { "nodes.invalid.key.[key]": ["Invalid key"] }, formErrors: [] }, new Map([["invalid.key", "entry"]]));
  expect(projectStudioIssues(captured, values.entries, (step) => configFields.get(step) ?? [])).toEqual({ fieldErrors: { "entries.0.key": ["Invalid key"] }, formErrors: [] });
});

test("editing drops only that node's path and retains the issue origin and form failures", () => {
  const captured = captureStudioIssues({ fieldErrors: {
    "nodes.first.config.target": ["Target"], "nodes.first.label": ["Label"], "nodes.second.config.target": ["Other"],
  }, formErrors: ["Load failed"] }, new Map([["first", "first"], ["second", "second"]]), "publish");
  const edited = dropStudioIssue(captured, "first", "config.target");
  expect(edited.nodes.first).toEqual({ label: ["Label"] });
  expect(edited.nodes.second).toEqual({ "config.target": ["Other"] });
  expect(edited.origin).toBe("publish");
  expect(edited.formErrors).toEqual(["Load failed"]);
});
