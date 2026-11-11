import { useMemo } from "react";
import { operationDocuments } from "@angee/gql/console/actions";
import { GraphQLError, storyAppearance, type RuntimeFixtureProps, RoutedRuntimeFixture, storySchema, type GraphQLWireRequest } from "@angee/storybook/testing";
import { createRouteHref } from "@angee/ui";

import { WorkflowsPage } from "./WorkflowsPage";
import { workflowFixture, workflowVersionFixture } from "./catalogue/testing";
import { runFixture, runResourceFixture, runSubjectFixture, workflowResourceFixture, stepRunResourceFixture, attemptResourceFixture, recordResourceFixture, userResourceFixture } from "./testing";
import { triggerFixture, triggerResourceFixture } from "./trigger-testing";

export default { title: "Workflows/Catalogue", parameters: { angeeOwnRuntime: true, layout: "fullscreen" }, excludeStories: ["CatalogueStory"] };
export const Catalogue = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <CatalogueStory appearance={storyAppearance(globals)} list /> };
export const Workflow = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <CatalogueStory appearance={storyAppearance(globals)} /> };
export const Author = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <CatalogueStory appearance={storyAppearance(globals)} writer /> };
export const Unavailable = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <CatalogueStory appearance={storyAppearance(globals)} unavailable /> };
export const QueryError = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <CatalogueStory appearance={storyAppearance(globals)} queryError /> };

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

export function CatalogueStory({ appearance, list = false, unavailable = false, queryError = false, writer = false, onRequest }: {
  appearance?: RuntimeFixtureProps["appearance"];
  list?: boolean; unavailable?: boolean; queryError?: boolean; writer?: boolean;
  onRequest?: (request: GraphQLWireRequest) => void;
}) {
  const schemas = useMemo(() => {
    const fixture = storySchema({
      workflow_step_outcomes: () => [{ node: "entry", outcomes: { done: "Done", error: "Error" }, issues: [] }],
      workflow_step_choices: () => [{ key: "echo", label: "Echo", icon: "", category: "", defaults: {}, config_schema: null, internal: false, outcomes: { done: "Done", error: "Error" }, issues: [] }],
      // The studio's own read carries the authoring draft; the page's record read carries the viewer's permissions.
      workflow_by_pk: (_args, { operationName }) => {
        if (operationName === "WorkflowStudio") return { ...workflowFixture, permissions: ["monitor", "write"], draft_revision: 1, layout: { entry: [80, 60] },
          draft: { nodes: { entry: { step: "echo", label: "Entry" } } } };
        if (queryError) throw new GraphQLError("Could not read this workflow.");
        return unavailable ? null : { ...workflowFixture, permissions: writer ? ["monitor", "write"] : [] };
      },
      workflowversion: () => [{ id: "wfv_review", number: 2, created_at: "2026-09-29T09:00:00Z", published_by: "usr_operator", content_hash: "retained_hash" }],
      workflowversion_aggregate: () => ({ aggregate: { count: 1 } }),
      // The leaf groups read carries each group's first page.
      workflowrun_groups: (_args, { variables }) => [
        { key: { status: "FAILED", workflow_id: "wfl_review", workflow__name: "Record review" }, aggregate: { count: 1 },
          ...(variables.with_rows ? { rows: [runFixture()] } : {}) },
      ],
      workflowrun_groups_count: () => 1,
      workflowrun: () => [runFixture()],
      workflowrun_aggregate: () => ({ aggregate: { count: 1 } }),
      trigger: () => [triggerFixture],
      trigger_aggregate: () => ({ aggregate: { count: 1 } }),
      notes_by_pk: () => ({ id: "nte_7", display_name: "Review notes" }),
      workflow: () => [workflowFixture],
      workflow_aggregate: () => ({ aggregate: { count: 1 } }),
    }, onRequest).public!;
    return { public: fixture, console: { ...fixture, metadata: { angee: { resources: [
      workflowResourceFixture, workflowVersionFixture, runResourceFixture, runSubjectFixture,
      stepRunResourceFixture, attemptResourceFixture, recordResourceFixture, userResourceFixture,
      triggerResourceFixture,
    ] } } } };
  }, [unavailable, queryError, onRequest, writer]);
  return <RoutedRuntimeFixture appearance={appearance} activeSchema="console" schemas={schemas} collectionPath="/workflows"
    initialEntry={list ? "/workflows" : "/workflows/wfl_review"} runtime={runtime} resourceName="workflows.Workflow" resourceLabel="Workflows" operationDocuments={documents}>
    <WorkflowsPage />
  </RoutedRuntimeFixture>;
}
