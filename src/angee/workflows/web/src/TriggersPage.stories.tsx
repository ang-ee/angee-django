import { useMemo } from "react";
import { operationDocuments } from "@angee/gql/console/actions";
import { composeAddons } from "@angee/app";
import { storyAppearance, type RuntimeFixtureProps, RoutedRuntimeFixture, storySchema, type GraphQLWireRequest } from "@angee/storybook/testing";
import { createRouteHref, defaultWidgets } from "@angee/ui";
import { TriggerCondition } from "./TriggerCondition";
import { TriggersList } from "./TriggersPage";
import { TRIGGER_MODEL } from "./triggers";
import { triggerEventFixture, triggerEventResourceFixture, triggerFixture, triggerMessageResourceFixture, triggerNoteResourceFixture, triggerResourceFixture } from "./trigger-testing";
import { runResourceFixture, userResourceFixture, workflowResourceFixture } from "./testing";
import addon from "./index";

export default { title: "Workflows/Triggers", parameters: { angeeOwnRuntime: true, layout: "fullscreen" }, excludeStories: ["TriggerStory"] };
export const Trigger = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <TriggerStory appearance={storyAppearance(globals)} /> };
export const WorkflowTriggers = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <TriggerStory appearance={storyAppearance(globals)} embedded /> };
export const ReadOnly = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <TriggerStory appearance={storyAppearance(globals)} canEdit={false} /> };

const documents = { console: operationDocuments };
const runtime = {
  containers: composeAddons([{ id: addon.id, containers: {
    [`${TRIGGER_MODEL}#sections`]: addon.containers?.[`${TRIGGER_MODEL}#sections`] ?? {},
  } }], { canonicalModelLabel: (model) => model }).containers,
  widgets: { ...defaultWidgets, "angee.workflows.condition": { read: TriggerCondition, edit: TriggerCondition } },
  routeHref: createRouteHref([
    { name: "workflows.triggers", path: "/workflows/triggers" },
    { name: "workflows.triggers.record", path: "/workflows/triggers/$id" },
    { name: "workflows.trigger-events", path: "/workflows/trigger-events" },
    { name: "workflows.trigger-events.record", path: "/workflows/trigger-events/$id" },
    { name: "workflows.catalogue.record", path: "/workflows/$id" },
    { name: "workflows.runs.record", path: "/workflows/runs/$id" },
    { name: "notes.record", path: "/notes/$id" },
  ]),
  routesByResource: {
    "workflows.Trigger": { collection: "workflows.triggers", record: { name: "workflows.triggers.record", param: "id" } },
    "workflows.TriggerEvent": { collection: "workflows.trigger-events", record: { name: "workflows.trigger-events.record", param: "id" } },
    "workflows.Workflow": { collection: "workflows.catalogue", record: { name: "workflows.catalogue.record", param: "id" } },
    "workflows.WorkflowRun": { collection: "workflows.runs", record: { name: "workflows.runs.record", param: "id" } },
    "notes.Note": { collection: "notes", record: { name: "notes.record", param: "id" } },
  },
};

/** Native form and actions over retained trigger rows, with generated mutation documents. */
export function TriggerStory({ appearance, embedded = false, canEdit = true, onRequest }: {
  appearance?: RuntimeFixtureProps["appearance"]; embedded?: boolean; canEdit?: boolean; onRequest?: (request: GraphQLWireRequest) => void }) {
  const schemas = useMemo(() => {
    let current = { ...triggerFixture, can_edit: canEdit, enable_preview: canEdit ? triggerFixture.enable_preview : null };
    const result = (message: string) => ({ ok: true, message, id: current.id });
    const fixture = storySchema({
      impl_choices: () => ["record_changed", "custom_changed", "message_ingested"].map((key) => ({ key, label: key, defaults: { source_model: key === "message_ingested" ? "messaging.Message" : "" }, config_schema: null })),
      revoke_workflow_trigger_grant: () => {
        current = { ...current, enabled: false, grants: [], disabled_reason: "The workflow principal's member grant was revoked; enable this trigger again." };
        return result(current.disabled_reason);
      },
      enable_workflow_trigger: () => { current = { ...current, enabled: true }; return result("Trigger enabled."); },
      disable_workflow_trigger: () => { current = { ...current, enabled: false }; return result("Trigger disabled."); },
      insert_trigger_one: () => current,
      update_trigger_by_pk: () => current,
      trigger_by_pk: () => current,
      trigger: () => [current],
      trigger_aggregate: () => ({ aggregate: { count: 1 } }),
      triggerevent: () => [triggerEventFixture],
      triggerevent_aggregate: () => ({ aggregate: { count: 1 } }),
      workflow_by_pk: () => current.workflow,
      workflowrun_by_pk: () => triggerEventFixture.started_run,
      notes_by_pk: () => ({ id: "nte_7", display_name: "Review notes" }),
    }, onRequest).public!;
    return { public: fixture, console: { ...fixture, metadata: { angee: { resources: [
      triggerResourceFixture, triggerEventResourceFixture, workflowResourceFixture, runResourceFixture, userResourceFixture, triggerNoteResourceFixture, triggerMessageResourceFixture,
    ] } } } };
  }, [canEdit, onRequest]);
  return <RoutedRuntimeFixture appearance={appearance} activeSchema="console" schemas={schemas} collectionPath="/workflows/triggers"
    initialEntry={embedded ? "/workflows/triggers" : "/workflows/triggers/wft_review"} runtime={runtime}
    resourceName={TRIGGER_MODEL} resourceLabel="Triggers" operationDocuments={documents}>
    <TriggersList workflowId={embedded ? "wfl_review" : undefined} />
  </RoutedRuntimeFixture>;
}
