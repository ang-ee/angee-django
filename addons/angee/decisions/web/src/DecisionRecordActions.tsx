import type { ActionFieldName } from "@angee/gql/console/actions";
import type { DocumentType } from "@angee/gql/console";
import { useAuthoredQuery } from "@angee/refine";
import {
  Button, DialogForm, ErrorBanner, LabeledDescriptorField, RecordActionBar, RecordActionTrigger,
  formSpecInitialValues, normalizeFormSpecValues, useActionForm, useActionOutcomeMutation,
  useActionResultRun, useAppRuntime, useRecordAction, useRecordChromeContext, useRuntimeViewAs,
} from "@angee/ui";
import { Controller } from "react-hook-form";
import * as React from "react";

import { DecisionRecordDocument } from "./documents";
import { decisionFormActions, type DecisionFormAction } from "./decision-form";
import { useDecisionsT } from "./i18n";
import { DECISION_MODEL } from "./resources";

type Decision = NonNullable<DocumentType<typeof DecisionRecordDocument>["decisions_by_pk"]>;

/** Frozen actions and successor admission are inherited by any decision form. */
export function DecisionRecordActions(): React.ReactElement | null {
  const { recordId, dataProviderName } = useRecordChromeContext();
  const query = useAuthoredQuery(DecisionRecordDocument, { id: recordId }, { dataProviderName, models: [DECISION_MODEL] });
  const t = useDecisionsT();
  if (query.error) return <ErrorBanner title={t("decisions.error")} description={query.error.message} />;
  return query.data?.decisions_by_pk ? <DecisionActions key={query.data.decisions_by_pk.id} decision={query.data.decisions_by_pk} /> : null;
}

function DecisionActions({ decision }: { decision: Decision }): React.ReactElement | null {
  const t = useDecisionsT();
  const { widgets } = useAppRuntime();
  const [selected, setSelected] = React.useState<DecisionFormAction | null>(null);
  const [revisit] = useActionOutcomeMutation<ActionFieldName>("revisit_human_decision", { invalidateModels: [DECISION_MODEL] });
  const settle = useActionResultRun({ linkTo: DECISION_MODEL });
  const runRevisit = useRecordAction(async (id) => { await settle(() => revisit(id, { revision: decision.revision })); });
  if (!decision.permissions.includes("act")) return null;
  let actions: readonly DecisionFormAction[] = [];
  try {
    if (decision.is_open) actions = decisionFormActions(decision.form_schema, widgets);
  } catch {
    return <ErrorBanner description={t("decision.schemaError")} />;
  }
  return <>
    {actions.map((action) => <RecordActionTrigger key={action.value}
      onClick={() => setSelected(action)}>{action.label}</RecordActionTrigger>)}
    <RecordActionBar record={decision} actions={[{
      id: "revisit", label: t("decision.revisit"), run: runRevisit,
      visibleWhen: () => decision.can_revisit,
      confirm: { title: t("decision.revisit.title"), body: t("decision.revisit.body") },
    }]} />
    {selected ? <DecisionActionDialog key={`${decision.id}:${selected.value}`}
      decision={decision} action={selected} onClose={() => setSelected(null)} /> : null}
  </>;
}

function DecisionActionDialog({ decision, action, onClose }: {
  decision: Decision; action: DecisionFormAction; onClose: () => void;
}): React.ReactElement {
  const t = useDecisionsT();
  const preview = useRuntimeViewAs();
  const [revision] = React.useState(decision.revision);
  const [decide] = useActionOutcomeMutation<ActionFieldName>("decide_human_decision", { invalidateModels: [DECISION_MODEL] });
  const form = useActionForm<Record<string, unknown>>({
    defaultValues: formSpecInitialValues(action.fields, {}), fieldNames: action.fields.map((field) => field.name),
    submit: async (values) => (await decide(decision.id, {
      revision, action: action.value, values: normalizeFormSpecValues(action.fields, values),
    })) ?? null,
    onSuccess: onClose,
  });
  const blocked = form.submitting || Boolean(preview.viewAs || preview.pending) || !decision.is_open;
  return <DialogForm open onOpenChange={(open) => { if (!open) onClose(); }} title={action.label}
    onSubmit={(event) => { event.preventDefault(); if (!blocked) void form.run(form.form.getValues()); }}
    footer={<><Button variant="ghost" onClick={onClose}>{t("decision.cancel")}</Button>
      <Button type="submit" disabled={blocked} loading={form.submitting}>{action.label}</Button></>}>
    {action.fields.map((field) => <Controller key={field.name} control={form.form.control} name={field.name}
      render={({ field: binding }) => <LabeledDescriptorField field={field} value={binding.value}
        readOnly={blocked || field.readOnly} messages={form.fieldErrors[field.name] ?? []}
        onChange={(value) => { form.clearFieldError(field.name); binding.onChange(value); }} />} />)}
    <ErrorBanner description={form.formError} />
  </DialogForm>;
}
