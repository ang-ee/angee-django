import { useMemo } from "react";
import * as v from "valibot";
import { operationDocuments } from "@angee/gql/console/actions";
import { RoutedRuntimeFixture, jsonResponse, storySchema } from "@angee/storybook/testing";
import { createRouteHref, defaultWidgets, JsonValueSchema } from "@angee/ui";
import { TriggerCondition } from "./TriggerCondition";
import { TriggersList } from "./TriggersPage";
import { TRIGGER_MODEL } from "./triggers";
import { triggerEventFixture, triggerEventResourceFixture, triggerFixture, triggerMessageResourceFixture, triggerNoteResourceFixture, triggerResourceFixture } from "./trigger-testing";
import { runResourceFixture, userResourceFixture, workflowResourceFixture } from "./testing";

export default { title: "Workflows/Triggers", parameters: { layout: "fullscreen" }, excludeStories: ["TriggerStory"] };
export const Trigger = { render: () => <TriggerStory /> };
export const WorkflowTriggers = { render: () => <TriggerStory embedded /> };
export const ReadOnly = { render: () => <TriggerStory canEdit={false} /> };

const RequestSchema = v.object({ query: v.string(), variables: v.optional(v.record(v.string(), JsonValueSchema), {}) });
export type TriggerRequest = v.InferOutput<typeof RequestSchema>;
const documents = { console: operationDocuments };
const runtime = {
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
export function TriggerStory({ embedded = false, canEdit = true, onRequest }: { embedded?: boolean; canEdit?: boolean; onRequest?: (request: TriggerRequest) => void }) {
  const schemas = useMemo(() => {
    let current = { ...triggerFixture, can_edit: canEdit, enable_preview: canEdit ? triggerFixture.enable_preview : null };
    const fixture = storySchema(async (_input, init) => {
      const request = v.parse(RequestSchema, JSON.parse(String(init?.body ?? "{}")));
      onRequest?.(request);
      const { query } = request;
      if (query.includes("impl_choices")) return jsonResponse({ data: { impl_choices: ["record_changed", "custom_changed", "message_ingested"].map((key) => ({ key, label: key, defaults: { source_model: key === "message_ingested" ? "messaging.Message" : "" }, config_schema: null })) } });
      if (query.includes("revoke_workflow_trigger_grant(")) {
        current = { ...current, enabled: false, grants: [], disabled_reason: "The workflow principal's member grant was revoked; enable this trigger again." };
        return jsonResponse({ data: { revoke_workflow_trigger_grant: { ok: true, message: current.disabled_reason, id: current.id } } });
      }
      const action = ["enable_workflow_trigger", "disable_workflow_trigger"].find((name) => query.includes(name + "("));
      if (action) {
        current = { ...current, enabled: action === "enable_workflow_trigger" };
        return jsonResponse({ data: { [action]: { ok: true, message: current.enabled ? "Trigger enabled." : "Trigger disabled.", id: current.id } } });
      }
      if (query.includes("insert_trigger_one")) return jsonResponse({ data: { insert_trigger_one: current } });
      if (query.includes("update_trigger_by_pk")) return jsonResponse({ data: { update_trigger_by_pk: current } });
      if (query.includes("trigger_by_pk")) return jsonResponse({ data: { trigger_by_pk: current } });
      if (query.includes("triggerevent")) return jsonResponse({ data: { triggerevent: [triggerEventFixture], triggerevent_aggregate: { aggregate: { count: 1 } } } });
      if (query.includes("workflow_by_pk")) return jsonResponse({ data: { workflow_by_pk: current.workflow } });
      if (query.includes("workflowrun_by_pk")) return jsonResponse({ data: { workflowrun_by_pk: triggerEventFixture.started_run } });
      if (query.includes("notes_by_pk")) return jsonResponse({ data: { notes_by_pk: { id: "nte_7", display_name: "Review notes" } } });
      return jsonResponse({ data: { trigger: [current], trigger_aggregate: { aggregate: { count: 1 } } } });
    }).public!;
    return { public: fixture, console: { ...fixture, metadata: { angee: { resources: [
      triggerResourceFixture, triggerEventResourceFixture, workflowResourceFixture, runResourceFixture, userResourceFixture, triggerNoteResourceFixture, triggerMessageResourceFixture,
    ] } } } };
  }, [canEdit, onRequest]);
  return <RoutedRuntimeFixture activeSchema="console" schemas={schemas} collectionPath="/workflows/triggers"
    initialEntry={embedded ? "/workflows/triggers" : "/workflows/triggers/wft_review"} runtime={runtime}
    resourceName={TRIGGER_MODEL} resourceLabel="Triggers" operationDocuments={documents}>
    <TriggersList workflowId={embedded ? "wfl_review" : undefined} />
  </RoutedRuntimeFixture>;
}
