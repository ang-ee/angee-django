import { useState, type ReactElement } from "react";
import * as v from "valibot";
import type { ActionFieldName } from "@angee/gql/console/actions";
import { holdsPermission } from "@angee/metadata";
import {
  Button, ErrorBanner, LabeledDescriptorField, RecordReference,
  actionOutcomeSubmitResult, formSubmitError, useActionOutcomeMutation,
} from "@angee/ui";

import { DecisionContext } from "./DecisionContext";
import { DECISION_MODELS, type Decision } from "./documents.console";
import { useDecisionsT } from "./i18n";
import { ProposalSchema } from "./proposal";
import { DecisionProvider, DecisionOriginOutlet } from "./origin";

/** One question, its alternatives, concerned records, evidence and chosen verdict. */
export function DecisionCard({ decision, onAnswered }: {
  decision: Decision; onAnswered?: () => void | Promise<unknown>;
}): ReactElement {
  const t = useDecisionsT();
  const open = decision.verdict === null;
  const verdict = v.safeParse(v.array(v.string()), decision.verdict);
  const [chosen, setChosen] = useState<string[]>([]);
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [decide] = useActionOutcomeMutation<ActionFieldName>("decide", {
    dataProviderName: "console", invalidateModels: DECISION_MODELS,
  });
  const proposal = v.safeParse(ProposalSchema, decision.proposal);
  const canAnswer = open && holdsPermission(decision, "act");
  const choose = (key: string, multiple: boolean) => {
    setChosen((current) => multiple
      ? current.includes(key) ? current.filter((value) => value !== key) : [...current, key]
      : [key]);
  };
  const submit = async () => {
    setBusy(true);
    setError(undefined);
    try {
      const result = await decide(decision.id, { revision: decision.revision, chosen })
        .then(actionOutcomeSubmitResult).catch(formSubmitError);
      if (result.status === "ok") await onAnswered?.();
      else setError(result.status === "conflict" ? t("decision.conflict") : [...result.issues.formErrors, ...Object.values(result.issues.fieldErrors).flat()].join(" "));
    } finally { setBusy(false); }
  };
  return <DecisionProvider value={{ decision }}><article aria-label={decision.kind_label} className="min-w-0 space-y-4 [overflow-wrap:anywhere]">
    <h2 className="font-semibold">{decision.kind_label}</h2>
    <DecisionOriginOutlet />
    <div className="grid gap-4 sm:grid-cols-2">{[
      { name: "requester", label: t("decision.requester"), widget: "text", value: decision.requester?.display_name },
      { name: "answered_by", label: t("decision.answeredBy"), widget: "text", value: decision.answered_by?.display_name },
      { name: "answered_at", label: t("decision.answeredAt"), widget: "datetime", value: decision.answered_at },
    ].filter(({ value }) => value != null).map(({ value, ...field }) =>
      <LabeledDescriptorField key={field.name} field={field} value={value} readOnly onChange={() => {}} />)}</div>
    <ul aria-label={t("decision.records")} className="flex flex-wrap gap-2">
      {decision.records.map((record) => record.record_model && record.record_id
        ? <li key={record.id}><RecordReference model={record.record_model} id={record.record_id} /></li> : null)}
    </ul>
    {proposal.success ? <fieldset aria-label={t("decision.proposal")} disabled={busy || !canAnswer} className="space-y-3">
      <legend>{t("decision.proposal")}</legend>
      {proposal.output.alternatives.map((alternative) => {
        const checked = open ? chosen.includes(alternative.key) : verdict.success && verdict.output.includes(alternative.key);
        return <section key={alternative.key} className="space-y-2">
          <label className="flex items-center gap-2">
            <input type={proposal.output.multiple ? "checkbox" : "radio"} name={`decision-${decision.id}`}
              value={alternative.key} checked={checked} onChange={() => choose(alternative.key, proposal.output.multiple)} />
            <span className={checked ? "font-semibold" : undefined}>{alternative.label}</span>
          </label>
          {Object.entries(alternative.actions).map(([id, actions]) => {
            const record = decision.records.find((record) => record.record_id === id);
            return <div key={id} className="space-y-2">
              {record?.record_model && record.record_id ? <RecordReference model={record.record_model} id={record.record_id} /> : null}
              <dl>{Object.entries(actions.fields).map(([field, operation]) =>
                <div key={field}><dt className="font-medium">{field}</dt><dd>{JSON.stringify(operation.set)}</dd></div>)}</dl>
              {actions.record ? <p>{actions.record.call}</p> : null}
            </div>;
          })}
        </section>;
      })}
    </fieldset> : <ErrorBanner description={t("decision.invalidProposal")} />}
    <DecisionContext context={decision.context} />
    {error ? <ErrorBanner description={error} /> : null}
    {canAnswer && proposal.success ? <Button disabled={busy || !chosen.length} onClick={() => void submit()}>
      {t("decision.submit")}</Button> : null}
  </article></DecisionProvider>;
}
