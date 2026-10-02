import { expect, test } from "vitest";
import { createKeyedEntry } from "@angee/ui";
import { studioErrors, studioSnapshot, studioValues } from "./studio-state";

test("renames references and layout at save while retaining literal values and unknown declarations", () => {
  const values = studioValues({ nodes: {
    first: { step: "echo", input: { value: { from: "first" } }, config: { source: "first" }, next: { done: "second" } },
    second: { step: "echo", input: { from: "first", path: "value" }, body: { step: "echo", config: { from: "first" }, input: { from: "first" } } },
  }, results: [{ from: "second" }], outcome_labels: { done: "Complete" } }, { first: [10, 20], second: [30, 40] });
  values.entries[0]!.key = "renamed";
  const result = studioSnapshot(values);
  expect(result.status).toBe("ok");
  if (result.status !== "ok") return;
  expect(result.data.draft).toMatchObject({ nodes: {
    renamed: { input: { value: { from: "first" } }, config: { source: "first" }, next: { done: ["second"] } },
    second: { input: { from: "renamed" }, body: { config: { from: "first" }, input: { from: "renamed" } } },
  }, outcome_labels: { done: "Complete" } });
  expect(result.data.layout).toEqual({ renamed: [10, 20], second: [30, 40] });
  expect(values.entries[0]!.clientId).toBe("first");
});

test("duplicate keys and server issues bind stable client identities after reorder", () => {
  const values = studioValues({ nodes: { first: { step: "echo" }, second: { step: "echo" } } }, {});
  values.entries[1]!.key = "first";
  expect(studioSnapshot(values)).toMatchObject({ status: "invalid", issues: { fieldErrors: {
    "entries.0.key": ["Duplicate collection key: first"], "entries.1.key": ["Duplicate collection key: first"],
  } } });
  values.entries.reverse();
  expect(studioErrors({ fieldErrors: { "nodes.renamed.config.target": ["Missing"] }, formErrors: [] }, values.entries,
    new Map([["renamed", "first"]]))).toEqual({ fieldErrors: { "entries.1.value.config.target": ["Missing"] }, formErrors: [] });
});

test("new stable client identities become authored keys in fan-out and results", () => {
  const values = studioValues({ nodes: { first: { step: "echo" } }, results: [] }, {});
  const entry = createKeyedEntry({ step: "echo", label: "New", config: {}, next: {} }, "created");
  values.entries.push(entry);
  values.entries[0]!.value.next.done = [entry.clientId];
  values.document.results = [{ from: entry.clientId }];
  expect(studioSnapshot(values)).toMatchObject({ status: "ok", data: { draft: {
    nodes: { first: { next: { done: ["created"] } } }, results: [{ from: "created" }],
  } } });
});
