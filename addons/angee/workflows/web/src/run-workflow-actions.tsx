import * as React from "react";
import type { ActionFieldName } from "@angee/gql/console/actions";
import { useAuthoredQuery } from "@angee/refine";
import {
  DropdownMenu, RecordActionBar, parseFormSpec, useActionOutcomeMutation, useActionResultRun,
  useAppRuntime, useRecordChromeContext, type ActionDescriptor,
} from "@angee/ui";
import { modelChain } from "@angee/ui/runtime";
import { jsonSchemaActionArgs } from "@angee/ui/views/json-schema";

import { RUN_MODELS, StartableRecordWorkflowsDocument } from "./documents.console";
import { WORKFLOW_MODEL } from "./catalogue/resources";
import { useWorkflowsT } from "./i18n";

/** Every saved record offers the published workflows its actor may start. */
export function RunWorkflowRecordActions(): React.ReactElement | null {
  const chrome = useRecordChromeContext();
  const t = useWorkflowsT();
  const { widgets } = useAppRuntime();
  const models = React.useMemo(() => modelChain(chrome.canonicalResource, chrome.resource),
    [chrome.canonicalResource, chrome.resource]);
  const workflows = useAuthoredQuery(StartableRecordWorkflowsDocument, { models: [...models] }, {
    models: [WORKFLOW_MODEL, "workflows.WorkflowVersion"], enabled: Boolean(chrome.record),
  });
  const [start] = useActionOutcomeMutation<ActionFieldName>("start_workflow_run", {
    idArgument: "workflow_id", dataProviderName: "console",
    invalidateModels: [...RUN_MODELS, "decisions.Decision", "decisions.DecisionRecord"],
  });
  const settle = useActionResultRun({ noResultTitle: t("action.startFailed") });
  const actions = React.useMemo<readonly ActionDescriptor[]>(() =>
    (workflows.data?.workflow ?? []).flatMap((workflow) => {
      const published = workflow.published;
      // A null contract means the published graph cannot be started (its steps are gone).
      if (!published || published.input_schema == null) return [];
      const schema = parseFormSpec(published.input_schema);
      // An opening retains its key across submissions, including transport retries.
      let requestKey: string | undefined;
      const submit = async (values: Record<string, unknown>) => {
        const outcome = await start(workflow.id, {
          subject: { model: chrome.resource, id: chrome.recordId }, input: values,
          request_key: requestKey ??= crypto.randomUUID(),
        });
        if (outcome?.ok) requestKey = undefined;
        // The dialog's schema fields live inside the mutation's JSON input envelope.
        if (outcome?.validationErrors) return { ...outcome, validationErrors: Object.fromEntries(
          Object.entries(outcome.validationErrors).map(([path, messages]) =>
            [path.startsWith("input.") ? path.slice(6) : path, messages]),
        ) };
        return outcome;
      };
      return [{ id: workflow.id, label: workflow.name,
        ...(schema.required?.length ? {
          args: () => jsonSchemaActionArgs(published.input_schema, widgets), submit,
        } : { run: async () => { await settle(() => submit({})); } }),
      }];
    }), [workflows.data, chrome.resource, chrome.recordId, start, settle, widgets]);
  if (!chrome.record || !actions.length) return null;
  return <DropdownMenu.SubmenuRoot>
    <DropdownMenu.SubmenuTrigger disabled={chrome.actionsBlocked}>{t("action.runWorkflow")}</DropdownMenu.SubmenuTrigger>
    <DropdownMenu.Portal keepMounted>
      <DropdownMenu.Positioner side="right" align="start" sideOffset={6}>
        <DropdownMenu.Content>
          <RecordActionBar record={chrome.record} actions={actions} />
        </DropdownMenu.Content>
      </DropdownMenu.Positioner>
    </DropdownMenu.Portal>
  </DropdownMenu.SubmenuRoot>;
}
