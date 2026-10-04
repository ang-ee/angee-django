import { expect, test } from "vitest";
import { createAngeeI18nInstance, statusTone, type Tone, type UiTranslate } from "@angee/ui";
import { enWorkflowsMessages } from "./i18n";
import { projectRunGraph, RUN_GRAPH_EDGE_STYLES } from "./run-graph";
import { mappedRunGraphFixture, runGraphFixture, stepRunFixture, stepRunResourceFixture } from "./testing";
import { WORKFLOW_STEP_STATUS_TONES, WORKFLOW_STATUS_TONES } from "./status-tones";
import { formatStepPage } from "./step-page";

const i18n = createAngeeI18nInstance({ workflows: enWorkflowsMessages });
const t: UiTranslate = (key, vars) => i18n.t(key, { ns: "workflows", ...vars });
const enumOptions = (name: string) => (stepRunResourceFixture.fields ?? []).filter((field) => field.name === name)
  .flatMap((field) => (field.values ?? []).map((value) => ({ value: value.value, label: value.description })));
const options = { t, resolveTone: (value: string, override?: Record<string, Tone>) => statusTone(value, override, { statusTones: WORKFLOW_STATUS_TONES }), locale: "en",
  statusOptions: enumOptions("status"), waitOptions: enumOptions("waiting_kind"),
};

test("map summaries use full counts and attempts, with separate selection and current state", () => {
  const projection = projectRunGraph(mappedRunGraphFixture(), "reviews", options);
  const node = projection.nodes[0]!;
  expect(node).toMatchObject({ id: "reviews", kind: "waiting", title: "Review items", kindLabel: "Map",
    code: "reviews", selected: true, highlighted: true, ports: [{ id: "done", label: "Done" }, { id: "error", label: "Needs attention" }] });
  expect(projection.status.reviews).toEqual({ label: "Waiting", tone: "warning" });
  expect(typeof node.detail).toBe("string");
  expect(node.detail).toBe(new Intl.ListFormat("en", { style: "short", type: "unit" })
    .format(["119/122", "118 Succeeded", "3 Running", "1 Failed", "141 attempts"]));
  expect(node.ariaLabel).toContain("Review items");
  expect(node.ariaLabel).toContain("Waiting");
  expect(node.ariaLabel).toContain(String(node.detail));
  expect(Object.fromEntries(Object.entries(projection.nodeStyles).map(([kind, style]) => [kind, style.badgeTone])))
    .toEqual({ unreached: "neutral", ready: "success", running: "success", waiting: "warning",
      succeeded: "success", failed: "danger", canceled: "warning", skipped: "info" });
});

test("unreached nodes and empty maps remain visible without inventing a step row", () => {
  const graph = mappedRunGraphFixture();
  graph.nodes[0] = { ...graph.nodes[0]!, step_run: null, item_counts: [], item_attempts: 0 };
  const projection = projectRunGraph(graph, "finish", options);
  expect(projection.status.reviews?.label).toBe("Not reached");
  expect(projection.nodes[0]?.detail).toBe("");
  expect(projection.nodes[1]).toMatchObject({ kind: "unreached", selected: true, highlighted: false });
  expect(projection.status.finish?.label).toBe("Not reached");
  expect(projection.nodeStyles.unreached?.badgeTone).toBe("neutral");
  graph.nodes[0] = { ...graph.nodes[0]!, step_run: {
    ...runGraphFixture(stepRunFixture({ status: "READY", page_index: 0 })).nodes[0]!.step_run!,
    map_total: 0, map_settled: 0,
  } };
  const admitted = projectRunGraph(graph, undefined, options);
  expect(admitted.status.reviews?.label).toBe("Ready");
  expect(admitted.nodes[0]?.detail).toBe("");
});

test("paging, failure and waits project summary evidence without reading payloads", () => {
  expect(formatStepPage(0, t)).toBe("Page 1");
  const failed = projectRunGraph(runGraphFixture(), undefined, options);
  expect(failed.nodes[0]?.detail).toBe("The operation did not finish.");
  expect(failed.anchorNodeId).toBe("inspect");
  const paging = projectRunGraph(runGraphFixture(stepRunFixture({ status: "RUNNING", failure_reason: null,
    page_index: 3, outcome_label: "", waiting_kind: null })), undefined, options);
  expect(paging.status.inspect?.label).toBe("Running");
  expect(paging.nodes[0]?.detail).toBe("Page 4");
  expect(paging.nodes[0]?.highlighted).toBe(true);
  const waiting = projectRunGraph(runGraphFixture(stepRunFixture({ status: "WAITING", failure_reason: null,
    page_index: 0, waiting_kind: "RECORD", wait_reason: "Waiting for a change." })), undefined, options);
  expect(waiting.nodes[0]?.detail).toBe("Waiting for a change.");
});

test.each(["READY", "RUNNING", "WAITING"] as const)("a current %s node keeps page progress", (status) => {
  const projection = projectRunGraph(runGraphFixture(stepRunFixture({ status, page_index: 3,
    failure_reason: null, waiting_kind: null, outcome_label: "" })), undefined, options);
  expect(projection.nodes[0]?.detail).toBe("Page 4");
  expect(projection.nodes[0]?.highlighted).toBe(true);
});

test.each(["FAILED", "CANCELED", "SUCCEEDED", "SKIPPED"] as const)("a %s paged node shows its status without page progress", (status) => {
  const projection = projectRunGraph(runGraphFixture(stepRunFixture({ status, page_index: 576,
    failure_reason: null, waiting_kind: null, outcome_label: "" })), undefined, options);
  expect(projection.status.inspect?.label).toBe(status[0] + status.slice(1).toLowerCase());
  expect(projection.nodes[0]?.detail).toBe("");
});

test("waiting pages stay secondary, and canceled nodes anchor while settled runs fit the graph", () => {
  const waiting = projectRunGraph(runGraphFixture(stepRunFixture({ status: "WAITING", page_index: 3,
    failure_reason: null, waiting_kind: "RECORD", wait_reason: "Waiting for a change." })), undefined, options);
  expect(waiting.status.inspect?.label).toBe("Waiting");
  expect(waiting.nodes[0]?.detail).toBe("Page 4, Waiting for a change.");
  const canceled = runGraphFixture(stepRunFixture({ status: "SUCCEEDED" }));
  canceled.nodes[1] = { ...canceled.nodes[1]!, step_run: { ...canceled.nodes[0]!.step_run!, status: "CANCELED" } };
  expect(projectRunGraph(canceled, undefined, options).anchorNodeId).toBe("finish");
  expect(projectRunGraph(runGraphFixture(stepRunFixture({ status: "SUCCEEDED" })), undefined, options).anchorNodeId).toBeUndefined();
});

test("unreached, skipped and canceled nodes have distinct muted borders", () => {
  const styles = projectRunGraph(runGraphFixture(), undefined, options).nodeStyles;
  expect(styles.unreached?.badgeTone).toBe("neutral");
  expect(styles.skipped?.badgeTone).toBe("info");
  expect(styles.canceled?.badgeTone).toBe("warning");
  expect(new Set([styles.unreached?.borderColor, styles.skipped?.borderColor, styles.canceled?.borderColor]).size).toBe(3);
});

test.each([["CANCELED", "warning"], ["SKIPPED", "info"]] as const)(
  "%s has the same step tone for native enum badges and graph tokens", (status, tone) => {
    expect(options.resolveTone(status, WORKFLOW_STEP_STATUS_TONES)).toBe(tone);
    const projection = projectRunGraph(runGraphFixture(stepRunFixture({ status })), undefined, options);
    expect(projection.status.inspect?.tone).toBe(tone);
    expect(projection.nodeStyles[projection.nodes[0]!.kind]?.badgeTone).toBe(tone);
  },
);

test("failed nodes anchor before a lower ranked canceled node", () => {
  const graph = runGraphFixture(stepRunFixture({ status: "CANCELED" }));
  graph.nodes[1] = { ...graph.nodes[1]!, step_run: { ...graph.nodes[0]!.step_run!, status: "FAILED" } };
  expect(projectRunGraph(graph, undefined, options).anchorNodeId).toBe("finish");
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
