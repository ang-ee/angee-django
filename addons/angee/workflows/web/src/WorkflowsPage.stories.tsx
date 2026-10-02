import { useMemo } from "react";
import * as v from "valibot";
import { operationDocuments } from "@angee/gql/console/actions";
import { RoutedRuntimeFixture, jsonResponse, storySchema } from "@angee/storybook/testing";
import { createRouteHref, JsonValueSchema } from "@angee/ui";

import { WorkflowsPage } from "./WorkflowsPage";
import { workflowsChatter } from "./contributions";
import { workflowFixture, workflowVersionFixture } from "./catalogue/testing";
import { runFixture, runResourceFixture, runSubjectFixture, workflowResourceFixture, stepRunResourceFixture, attemptResourceFixture, artifactResourceFixture, userResourceFixture } from "./testing";
import { triggerFixture, triggerResourceFixture } from "./trigger-testing";

export default { title: "Workflows/Catalogue", parameters: { layout: "fullscreen" }, excludeStories: ["CatalogueStory"] };
export const Catalogue = { render: () => <CatalogueStory list /> };
export const Workflow = { render: () => <CatalogueStory /> };
export const Author = { render: () => <CatalogueStory writer /> };
export const Unavailable = { render: () => <CatalogueStory unavailable /> };
export const QueryError = { render: () => <CatalogueStory queryError /> };
export const RecordActivity = { render: () => <CatalogueStory record /> };

const RequestSchema = v.object({ query: v.string(), variables: v.optional(v.record(v.string(), JsonValueSchema), {}) });
const documents = { console: operationDocuments };
const runtime = {
  routeHref: createRouteHref([
    { name: "workflows.catalogue", path: "/workflows" },
    { name: "workflows.catalogue.record", path: "/workflows/$id" },
    { name: "workflows.runs", path: "/workflows/runs" },
    { name: "workflows.runs.record", path: "/workflows/runs/$id" },
    { name: "notes", path: "/notes" },
    { name: "notes.record", path: "/notes/$id" },
    { name: "workflows.triggers.record", path: "/workflows/triggers/$id" },
  ]),
  routesByResource: {
    "workflows.Workflow": { collection: "workflows.catalogue", record: { name: "workflows.catalogue.record", param: "id" } },
    "workflows.WorkflowRun": { collection: "workflows.runs", record: { name: "workflows.runs.record", param: "id" } },
    "notes.Note": { collection: "notes", record: { name: "notes.record", param: "id" } },
  },
};

export function CatalogueStory({ list = false, unavailable = false, queryError = false, record = false, writer = false, onRequest }: {
  list?: boolean; unavailable?: boolean; queryError?: boolean; record?: boolean; writer?: boolean;
  onRequest?: (request: v.InferOutput<typeof RequestSchema>) => void;
}) {
  const schemas = useMemo(() => {
    const fixture = storySchema(async (_input, init) => {
      const request = v.parse(RequestSchema, JSON.parse(String(init?.body ?? "{}")));
      onRequest?.(request);
      const { query } = request;
      if (query.includes("workflow_step_ports")) return jsonResponse({ data: { workflow_step_ports: [{ node: "entry", outcomes: { done: "Done", error: "Error" } }] } });
      if (query.includes("workflow_step_choices")) return jsonResponse({ data: {
        workflow_by_pk: { ...workflowFixture, permissions: ["write"], draft_revision: 1, layout: { entry: [80, 60] },
          draft: { nodes: { entry: { step: "echo", label: "Entry" } } }, draft_outcomes: { entry: { done: "Done" } } },
        workflow_step_choices: [{ key: "echo", label: "Echo", icon: "", category: "", defaults: {}, config_schema: null, internal: false, outcomes: { done: "Done", error: "Error" } }],
      } });
      if (query.includes("workflow_by_pk")) return queryError
        ? jsonResponse({ errors: [{ message: "Could not read this workflow." }] })
        : jsonResponse({ data: { workflow_by_pk: unavailable ? null : { ...workflowFixture, permissions: writer ? ["write"] : [] } } });
      if (query.includes("workflowversion")) return jsonResponse({ data: {
        workflowversion: [{ id: "wfv_review", number: 2, created_at: "2026-09-29T09:00:00Z", published_by: "usr_operator", content_hash: "retained_hash" }],
        workflowversion_aggregate: { aggregate: { count: 1 } },
      } });
      if (query.includes("workflowrun_groups")) return jsonResponse({ data: { workflowrun_groups: [
        { key: { status: "FAILED", workflow_id: "wfl_review", workflow__name: "Record review" }, aggregate: { count: 1 } },
      ], totalCount: 1 } });
      if (query.includes("workflowrun")) return jsonResponse({ data: { workflowrun: [runFixture()], workflowrun_aggregate: { aggregate: { count: 1 } } } });
      if (query.includes("trigger")) return jsonResponse({ data: { trigger: [triggerFixture], trigger_aggregate: { aggregate: { count: 1 } } } });
      if (query.includes("notes_by_pk")) return jsonResponse({ data: { notes_by_pk: { id: "nte_7", display_name: "Review notes" } } });
      return jsonResponse({ data: { workflow: [workflowFixture], workflow_aggregate: { aggregate: { count: 1 } } } });
    }).public!;
    return { public: fixture, console: { ...fixture, metadata: { angee: { resources: [
      workflowResourceFixture, workflowVersionFixture, runResourceFixture, runSubjectFixture,
      stepRunResourceFixture, attemptResourceFixture, artifactResourceFixture, userResourceFixture,
      triggerResourceFixture,
    ] } } } };
  }, [unavailable, queryError, onRequest, writer]);
  return <RoutedRuntimeFixture activeSchema="console" schemas={schemas} collectionPath="/workflows"
    initialEntry={list ? "/workflows" : "/workflows/wfl_review"} runtime={runtime} resourceName="workflows.Workflow" resourceLabel="Workflows" operationDocuments={documents}>
    {record ? workflowsChatter.render?.({ pathname: "/notes/nte_7", params: { id: "nte_7" },
      route: { name: "notes.record", path: "/notes/$id", viewType: "notes/note", canonicalLabel: "notes.Note" },
      view: { kind: "record", type: "notes/note", sqid: "nte_7" },
    }) : <WorkflowsPage />}
  </RoutedRuntimeFixture>;
}
