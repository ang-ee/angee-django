import { expect, test } from "vitest";
import { createAngeeI18nInstance, statusTone, type UiTranslate } from "@angee/ui";
import { enWorkflowsMessages } from "./i18n";
import { projectRunGraph, RUN_GRAPH_EDGE_STYLES } from "./run-graph";
import { mappedRunGraphFixture, runGraphFixture, stepRunFixture, stepRunResourceFixture } from "./testing";
import { WORKFLOW_STATUS_TONES } from "./status-tones";
import { formatStepPage } from "./step-page";

const i18n = createAngeeI18nInstance({ workflows: enWorkflowsMessages });
const t: UiTranslate = (key, vars) => i18n.t(key, { ns: "workflows", ...vars });
const enumOptions = (name: string) => (stepRunResourceFixture.fields ?? []).filter((field) => field.name === name)
  .flatMap((field) => (field.values ?? []).map((value) => ({ value: value.value, label: value.description })));
const options = { t, resolveTone: (value: string) => statusTone(value, undefined, { statusTones: WORKFLOW_STATUS_TONES }), locale: "en",
  statusOptions: enumOptions("status"), waitOptions: enumOptions("waiting_kind"),
};

test("map summaries use full counts and attempts, with separate selection and current state", () => {
  const projection = projectRunGraph(mappedRunGraphFixture(), "reviews", options);
  const node = projection.nodes[0]!;
  expect(node).toMatchObject({ id: "reviews", kind: "waiting", title: "Review items", kindLabel: "Map",
    code: "reviews", selected: true, highlighted: true, ports: [{ id: "done", label: "Done" }, { id: "error", label: "Needs attention" }] });
  expect(projection.status.reviews).toEqual({ label: "119/122", tone: "warning" });
  expect(typeof node.detail).toBe("string");
  expect(node.detail).toBe(new Intl.ListFormat("en", { style: "short", type: "unit" })
    .format(["118 Succeeded", "3 Running", "1 Failed", "141 attempts"]));
  expect(node.ariaLabel).toContain("Review items");
  expect(node.ariaLabel).toContain("Waiting");
  expect(node.ariaLabel).toContain(String(node.detail));
  for (const value of options.statusOptions) {
    const kind = value.value.toLowerCase();
    expect(projection.nodeStyles[kind]?.badgeTone).toBe(statusTone(value.value));
  }
});

test("unreached nodes and empty maps remain visible without inventing a step row", () => {
  const graph = mappedRunGraphFixture();
  graph.nodes[0] = { ...graph.nodes[0]!, step_run: null, item_counts: [], item_attempts: 0 };
  const projection = projectRunGraph(graph, "finish", options);
  expect(projection.status.reviews?.label).toBe("0/0");
  expect(projection.nodes[1]).toMatchObject({ kind: "unreached", selected: true, highlighted: false });
  expect(projection.status.finish?.label).toBe("Not reached");
  expect(projection.nodeStyles.unreached?.badgeTone).toBe("neutral");
});

test("paging, failure and waits project summary evidence without reading payloads", () => {
  expect(formatStepPage(0, t)).toBe("Page 1");
  const failed = projectRunGraph(runGraphFixture(), undefined, options);
  expect(failed.nodes[0]?.detail).toBe("The operation did not finish.");
  expect(failed.anchorNodeId).toBe("inspect");
  const paging = projectRunGraph(runGraphFixture(stepRunFixture({ status: "RUNNING", failure_reason: null,
    page_index: 3, outcome_label: "", waiting_kind: null })), undefined, options);
  expect(paging.status.inspect?.label).toBe("Page 4");
  expect(paging.nodes[0]?.highlighted).toBe(true);
  const waiting = projectRunGraph(runGraphFixture(stepRunFixture({ status: "WAITING", failure_reason: null,
    page_index: 0, waiting_kind: "RECORD", wait_reason: "Waiting for a change." })), undefined, options);
  expect(waiting.nodes[0]?.detail).toBe("Waiting for a change.");
});

test("the lowest ranked current node anchors the graph before any failed node", () => {
  const graph = runGraphFixture();
  graph.nodes.push({ ...graph.nodes[0]!, key: "late", rank: 10,
    step_run: { ...graph.nodes[0]!.step_run!, status: "RUNNING" } },
  { ...graph.nodes[0]!, key: "early", rank: 2,
    step_run: { ...graph.nodes[0]!.step_run!, status: "READY" } });
  graph.nodes.reverse();
  expect(projectRunGraph(graph, undefined, options).anchorNodeId).toBe("early");
});

test("routing uses declared outcome ports and stable edge identity across progress changes", () => {
  const graph = runGraphFixture();
  const idle = projectRunGraph(graph, undefined, options);
  graph.edges[0] = { ...graph.edges[0]!, taken: true };
  const taken = projectRunGraph(graph, undefined, options);
  expect(taken.edges[0]).toMatchObject({ source: "inspect", target: "finish", sourceHandle: "done", kind: "taken" });
  expect(taken.edges[0]?.id).toBe(idle.edges[0]?.id);
  expect(RUN_GRAPH_EDGE_STYLES.taken).toEqual({ stroke: "var(--brand)", strokeWidth: 2 });
});
