import * as React from "react";
import * as v from "valibot";
import { Link } from "@tanstack/react-router";
import { RoutedRuntimeFixture, jsonResponse, storySchema } from "@angee/storybook/testing";
import { ChatterTabsTestHost, ShellPageTestProviders } from "@angee/app/testing";
import { Button, JsonValueSchema, defaultWidgets, useChatter } from "@angee/ui";
import { WorkflowStudio } from "./WorkflowStudio";
import { runSubjectFixture, workflowResourceFixture } from "./testing";

export default { title: "Workflows/Studio", parameters: { layout: "fullscreen" }, excludeStories: ["StudioStory"] };
export const Draft = { render: () => <StudioStory /> };
export const Conflict = { render: () => <StudioStory conflict /> };
export const LocatedIssues = { render: () => <StudioStory invalid /> };
export const UnrenderedIssues = { render: () => <StudioStory unrenderedIssues /> };
export const ReadOnly = { render: () => <StudioStory readOnly /> };
export const Retained = { render: () => <StudioStory retained /> };
const Request = v.object({ query: v.string(), variables: v.optional(v.record(v.string(), JsonValueSchema), {}) });
const configuration = { type: "object", properties: {
  target: { type: "string", label: "Target record", relation: { resource: "notes.Note" } },
  threshold: { type: "integer", label: "Threshold", defaultValue: 1 },
}, required: ["target"] };
const choices = [
  { key: "echo", label: "Echo", icon: "", category: "", defaults: { config: { threshold: 1 } }, config_schema: configuration, internal: false, outcomes: { done: "Done", error: "Error" } },
  { key: "internal", label: "Internal", icon: "", category: "", defaults: {}, config_schema: null, internal: true, outcomes: { done: "Done", error: "Error" } },
];

export function StudioStory({ conflict = false, invalid = false, unrenderedIssues = false, failLatest = false, readOnly = false, outcomesGate, retained = false, linked = false, onRequest }: {
  conflict?: boolean; invalid?: boolean; unrenderedIssues?: boolean; failLatest?: boolean; readOnly?: boolean; retained?: boolean; linked?: boolean; outcomesGate?: () => Promise<void>; onRequest?: (request: v.InferOutput<typeof Request>) => void;
}) {
  const schemas = React.useMemo(() => {
    let revision = 1;
    let draft: unknown = { nodes: { entry: { step: "echo", label: "Entry", config: { target: "nte_7", threshold: 1 },
      ...(linked ? { next: { done: "second" } } : {}) },
      ...(linked ? { second: { step: "echo", label: "Second", config: {} } } : {}) }, results: [{ from: "entry" }] };
    let layout: unknown = { entry: [80, 60] };
    let number = 1;
    let refused = false;
    const publicSchema = storySchema(async (_input, init) => {
      const request = v.parse(Request, JSON.parse(String(init?.body ?? "{}")));
      onRequest?.(request);
      const { query, variables } = request;
      if (query.includes("save_workflow_draft")) {
        if (conflict && !refused) {
          refused = true; revision = 2;
          return jsonResponse({ errors: [{ message: "Draft changed since it was loaded.", extensions: { code: "STALE_REVISION", current_revision: revision } }] });
        }
        revision++; draft = variables.draft; layout = variables.layout;
        return jsonResponse({ data: { save_workflow_draft: { revision, diagnostics: [] } } });
      }
      if (query.includes("publish_workflow")) return invalid || unrenderedIssues
        ? jsonResponse({ errors: [{ message: "Invalid draft.", extensions: { code: "VALIDATION", validationErrors: unrenderedIssues
          ? { "nodes.entry.input": ["Missing binding."], "nodes.entry.config.unknown": ["Unknown config field."] }
          : { "nodes.entry.config.threshold": ["Threshold is too small."] }, formErrors: [] } }] })
        : jsonResponse({ data: { publish_workflow: { number: ++number, dependents: ["record_parent"] } } });
      if (query.includes("workflow_step_outcomes")) {
        await outcomesGate?.();
        const entries = v.parse(v.array(v.object({ node: v.string() })), variables.configurations);
        return jsonResponse({ data: { workflow_step_outcomes: entries.map((entry) => ({ node: entry.node, outcomes: { done: "Done", error: "Error" }, issues: [] })) } });
      }
      if (query.includes("workflow_step_choices") && failLatest && refused) return jsonResponse({ errors: [{ message: "Latest draft unavailable." }] });
      if (query.includes("workflow_step_choices")) return jsonResponse({ data: {
        workflow_by_pk: { id: "wfl_example", permissions: readOnly ? ["monitor"] : ["monitor", "write"], draft, draft_revision: revision, layout,
          published: { number } }, workflow_step_choices: choices,
      } });
      if (query.includes("notes_by_pk")) return jsonResponse({ data: { notes_by_pk: { id: "nte_7", display_name: "Selected record" } } });
      if (query.includes("notes")) return jsonResponse({ data: { notes: [{ id: "nte_8", display_name: "Another record" }], notes_aggregate: { aggregate: { count: 1 } } } });
      return jsonResponse({ data: {} });
    }).public!;
    return { console: { ...publicSchema, metadata: { angee: { resources: [workflowResourceFixture, runSubjectFixture] } } } };
  }, [conflict, invalid, unrenderedIssues, failLatest, readOnly, outcomesGate, linked, onRequest]);
  return <RoutedRuntimeFixture activeSchema="console" schemas={schemas} collectionPath="/studio">
    <ShellPageTestProviders runtime={{ widgets: defaultWidgets }}>
      <StudioDemo retained={retained} />
    </ShellPageTestProviders>
  </RoutedRuntimeFixture>;
}

function StudioDemo({ retained }: { retained: boolean }) {
  const [active, setActive] = React.useState(true);
  const pane = useChatter();
  return <>
    {retained ? <div>
      <Button onClick={() => setActive((value) => !value)}>{active ? "Hide studio" : "Show studio"}</Button>
      <Button onClick={() => pane.setCollapsed(true)}>Collapse inspector</Button>
      <Link to="/studio/other">Leave studio</Link>
      <output data-testid="pane-state">{String(pane.collapsed)}:{pane.activeTab}</output>
    </div> : null}
    <div className="grid grid-cols-[1fr_24rem] gap-4">
      <div hidden={!active}><WorkflowStudio recordId="wfl_example" active={active} /></div><ChatterTabsTestHost />
    </div>
  </>;
}
