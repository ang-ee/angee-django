import * as React from "react";
import * as v from "valibot";
import { RuntimeFixture, jsonResponse, storySchema } from "@angee/storybook/testing";
import { ChatterTabsTestHost, ShellPageTestProviders } from "@angee/app/testing";
import { JsonValueSchema, defaultWidgets } from "@angee/ui";
import { WorkflowStudio } from "./WorkflowStudio";
import { runSubjectFixture, workflowResourceFixture } from "./testing";

export default { title: "Workflows/Studio", parameters: { layout: "fullscreen" }, excludeStories: ["StudioStory"] };
export const Draft = { render: () => <StudioStory /> };
export const Conflict = { render: () => <StudioStory conflict /> };
export const LocatedIssues = { render: () => <StudioStory invalid /> };
const Request = v.object({ query: v.string(), variables: v.optional(v.record(v.string(), JsonValueSchema), {}) });
const configuration = { type: "object", properties: {
  target: { type: "string", label: "Target record", relation: { resource: "notes.Note" } },
  threshold: { type: "integer", label: "Threshold", defaultValue: 1 },
}, required: ["target"] };
const choices = [
  { key: "echo", label: "Echo", icon: "", category: "", defaults: { config: { threshold: 1 } }, config_schema: configuration, internal: false, outcomes: { done: "Done", error: "Error" } },
  { key: "internal", label: "Internal", icon: "", category: "", defaults: {}, config_schema: null, internal: true, outcomes: { done: "Done", error: "Error" } },
];

export function StudioStory({ conflict = false, invalid = false, failLatest = false, onRequest }: {
  conflict?: boolean; invalid?: boolean; failLatest?: boolean; onRequest?: (request: v.InferOutput<typeof Request>) => void;
}) {
  const schemas = React.useMemo(() => {
    let revision = 1;
    let draft: unknown = { nodes: { entry: { step: "echo", label: "Entry", config: { target: "nte_7", threshold: 1 } } }, results: [{ from: "entry" }] };
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
          return jsonResponse({ data: { save_workflow_draft: { status: "CONFLICT", message: "Draft changed since it was loaded.", issues: null, data: { revision, diagnostics: [] } } } });
        }
        revision++; draft = variables.draft; layout = variables.layout;
        return jsonResponse({ data: { save_workflow_draft: { status: "OK", message: "", issues: null, data: { revision, diagnostics: [] } } } });
      }
      if (query.includes("publish_workflow")) return jsonResponse({ data: { publish_workflow: invalid
        ? { status: "INVALID", data: null, message: "", issues: { fieldErrors: { "nodes.entry.config.threshold": ["Threshold is too small."] }, formErrors: [] } }
        : { status: "OK", data: { number: ++number, dependents: ["record_parent"] }, message: "", issues: null } } });
      if (query.includes("workflow_step_ports")) {
        const entries = v.parse(v.array(v.object({ node: v.string() })), variables.configurations);
        return jsonResponse({ data: { workflow_step_ports: entries.map((entry) => ({ node: entry.node, outcomes: { done: "Done", error: "Error" } })) } });
      }
      if (query.includes("workflow_step_choices") && failLatest && refused) return jsonResponse({ errors: [{ message: "Latest draft unavailable." }] });
      if (query.includes("workflow_step_choices")) return jsonResponse({ data: {
        workflow_by_pk: { id: "wfl_example", permissions: ["write"], draft, draft_revision: revision, layout,
          draft_outcomes: { entry: { done: "Done", error: "Error" } }, published: { number } }, workflow_step_choices: choices,
      } });
      if (query.includes("notes_by_pk")) return jsonResponse({ data: { notes_by_pk: { id: "nte_7", display_name: "Selected record" } } });
      if (query.includes("notes")) return jsonResponse({ data: { notes: [{ id: "nte_8", display_name: "Another record" }], notes_aggregate: { aggregate: { count: 1 } } } });
      return jsonResponse({ data: {} });
    }).public!;
    return { console: { ...publicSchema, metadata: { angee: { resources: [workflowResourceFixture, runSubjectFixture] } } } };
  }, [conflict, invalid, failLatest, onRequest]);
  return <RuntimeFixture activeSchema="console" schemas={schemas}>
    <ShellPageTestProviders runtime={{ widgets: defaultWidgets }}>
      <div className="grid grid-cols-[1fr_24rem] gap-4"><WorkflowStudio recordId="wfl_example" /><ChatterTabsTestHost /></div>
    </ShellPageTestProviders>
  </RuntimeFixture>;
}
