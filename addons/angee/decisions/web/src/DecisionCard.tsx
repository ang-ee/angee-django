import { useEffect, useRef, useState, type ReactElement } from "react";
import * as v from "valibot";
import type { ActionFieldName } from "@angee/gql/console/actions";
import { holdsPermission, useSchemaFieldMetadata, modelMetadataForLabel } from "@angee/metadata";
import {
  Badge, Button, Card, Checkbox, ErrorBanner, MetaGrid, RadioGroup, RecordReference,
  RelativeTime, StatusIcon, TimelineEntry, cn, radioGroupVariants,
  actionOutcomeSubmitResult, formSubmitError, titleCase, useActionOutcomeMutation, useActiveRecordForm, useRecordPeek,
} from "@angee/ui";
import { DecisionContext, DecisionContextSchema } from "./DecisionContext";
import { DECISION_MODELS, type Decision } from "./documents.console";
import { useDecisionsT } from "./i18n";
import { ProposalSchema } from "./proposal";
import { DecisionProvider, DecisionOriginOutlet } from "./origin";

export interface DecisionCardProps {
  decision: Decision;
  selfId?: string;
  highlighted?: boolean;
  compact?: boolean;
  inStep?: { records: readonly string[] };
  onEditField?: (field: string) => void;
  onAnswered?: () => void | Promise<unknown>;
}

/** One card in the inbox, a record timeline and a record selection. */
export function DecisionCard({ decision, selfId, highlighted, compact, inStep, onEditField, onAnswered }: DecisionCardProps): ReactElement {
  const t = useDecisionsT();
  const ref = useRef<HTMLElement>(null);
  const openRecord = useRecordPeek();
  const metadata = useSchemaFieldMetadata();
  const form = useActiveRecordForm();
  const fieldLabel = (id: string, field: string, model?: string) => (form?.id === id ? form.fieldLabel?.(field) : undefined)
    ?? modelMetadataForLabel(metadata, model ?? decision.records.find((record) => record.record_id === id)?.record_model ?? "")?.fields[field]?.label
    ?? titleCase(field);
  useEffect(() => {
    if (highlighted) ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [highlighted]);
  const open = decision.is_open;
  const verdict = v.safeParse(v.array(v.string()), decision.verdict);
  const [chosen, setChosen] = useState<string[]>([]);
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [decide] = useActionOutcomeMutation<ActionFieldName>("decide", {
    dataProviderName: "console", invalidateModels: DECISION_MODELS,
  });
  const proposal = v.safeParse(ProposalSchema, decision.proposal);
  const context = v.safeParse(DecisionContextSchema, decision.context);
  const references = context.success ? [...context.output.references, ...context.output.facts.flatMap((fact) => fact.evidence)] : [];
  const canAnswer = open && holdsPermission(decision, "act");
  const submit = async () => {
    setBusy(true); setError(undefined);
    try {
      const result = await decide(decision.id, { revision: decision.revision, chosen })
        .then(actionOutcomeSubmitResult).catch(formSubmitError);
      if (result.status === "ok") await onAnswered?.();
      else setError(result.status === "conflict" ? t("decision.conflict")
        : [...result.issues.formErrors, ...Object.values(result.issues.fieldErrors).flat()].join(" "));
    } finally { setBusy(false); }
  };
  const recordLink = (id: string) => {
    const record = decision.records.find((record) => record.record_id === id);
    const label = references.find((reference) => reference.id === id)?.label || undefined;
    return record?.record_model && record.record_id ? <RecordReference model={record.record_model} id={record.record_id} label={label}
      onOpen={() => openRecord({ model: record.record_model!, id: record.record_id! })} /> : null;
  };
  if (!open) {
    const withdrawn = verdict.success && verdict.output.length === 0;
    return <TimelineEntry as="div" title={withdrawn ? `Withdrawn: stopped by ${decision.answered_by?.display_name ?? "an operator"}`
      : decision.answered_by?.display_name ?? "Answered"} timestamp={decision.answered_at}
      body={withdrawn ? decision.kind_label : `Chose: ${decision.verdict_label}`} className={withdrawn ? "bg-sheet-2" : "bg-sheet"} />;
  }
  const changes = (alternative: v.InferOutput<typeof ProposalSchema>["alternatives"][number]) =>
    <span className="mt-1 grid gap-1.5">{Object.entries(alternative.actions).map(([id, actions]) =>
      <span key={id} className="grid gap-1">
        {id !== selfId ? recordLink(id) : null}
        {actions.record ? <span className="text-xs text-fg-2">{actions.record.call.replaceAll("_", " ")}</span> : null}
        <MetaGrid rows={Object.entries(actions.fields).map(([field, operation]) => ({
          id: field,
          label: fieldLabel(id, field, actions.model),
          value: (() => {
            const facts = modelMetadataForLabel(metadata, actions.model ?? decision.records.find((record) => record.record_id === id)?.record_model ?? "")?.fields[field];
            return facts?.relationModelLabel && typeof operation.set === "string" ? <RecordReference model={facts.relationModelLabel} id={operation.set} onOpen={() => openRecord({ model: facts.relationModelLabel!, id: operation.set as string })} />
              : operation.set === null ? "None" : typeof operation.set === "boolean" ? operation.set ? "Yes" : "No"
              : facts?.values?.find((value) => value.value === operation.set)?.description ?? (typeof operation.set === "string" ? operation.set : JSON.stringify(operation.set));
          })(),
        }))} />
      </span>)}</span>;
  const alternatives = proposal.success ? proposal.output.alternatives : [];
  const editField = alternatives.flatMap((alternative) => Object.keys(alternative.actions[selfId ?? ""]?.fields ?? {}))[0]
    ?? (proposal.success ? proposal.output.checks[selfId ?? ""]?.[0] : undefined);
  return <DecisionProvider value={{ decision }}><Card asChild className={cn("min-w-0 p-3 shadow-none", highlighted && "border-brand ring-2 ring-brand/30")}>
    <section ref={ref} aria-label={decision.kind_label} data-decision={decision.id}>
      <div className="flex min-w-0 items-start gap-2"><StatusIcon tone="warning" size="sm" icon="help" className="mt-0.5" />
        <div className="min-w-0 flex-1"><h2 className="text-13 font-semibold leading-5 text-fg [overflow-wrap:anywhere]">{decision.kind_label}</h2>
          {!inStep ? <div className="mt-0.5 text-xs text-fg-muted"><DecisionOriginOutlet />
            {decision.requester ? `Asked by ${decision.requester.display_name}` : null}{" · "}<RelativeTime value={decision.created_at} /></div> : null}
        </div>
      </div>
      <DecisionContext context={decision.context} reasonOnly />
      {proposal.success ? <>
        {Object.entries(proposal.output.checks).map(([id, fields]) => fields.map((field) =>
          <div key={`${id}:${field}`} className="mt-3 rounded-6 border border-danger-line bg-danger-tint px-2.5 py-2">
            <Badge tone="danger" density="compact">Check</Badge>{" "}{id !== selfId ? recordLink(id) : null}{" "}{fieldLabel(id, field)}
          </div>))}
        <div className="mt-3">{proposal.output.multiple ? <div role="group" aria-label="Alternatives, choose one or more" className="grid gap-1.5">
          {alternatives.map((alternative) => {
            const selected = chosen.includes(alternative.key); const styles = radioGroupVariants();
            const id = `${decision.id}-${alternative.key}`;
            return <div key={alternative.key} className={cn(styles.item(), "border", selected ? "border-brand bg-brand-tint" : "border-border-subtle")}>
              <Checkbox id={id} aria-label={alternative.label} checked={selected} disabled={busy || !canAnswer} onCheckedChange={(on) => setChosen((current) =>
                on ? [...new Set([...current, alternative.key])] : current.filter((key) => key !== alternative.key))} />
              <span className={styles.text()}><label htmlFor={id} className={styles.label()}>{alternative.label}</label>{changes(alternative)}</span>
            </div>;
          })}</div> : <RadioGroup aria-label="Alternatives, choose one" className="gap-1.5" value={chosen[0] ?? null}
            disabled={busy || !canAnswer} onValueChange={(value: unknown) => setChosen([String(value)])}>
            {alternatives.map((alternative) => <RadioGroup.Item key={alternative.key} value={alternative.key} label={alternative.label}
              className={cn("border", chosen[0] === alternative.key ? "border-brand bg-brand-tint" : "border-border-subtle")}
              description={changes(alternative)} />)}
          </RadioGroup>}</div>
      </> : <ErrorBanner description={t("decision.invalidProposal")} />}
      {error ? <ErrorBanner description={error} /> : null}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {canAnswer && proposal.success ? <Button size="sm" variant="primary" disabled={busy || !chosen.length} onClick={() => void submit()}>{t("decision.submit")}</Button> : null}
        {onEditField && editField ? <Button size="sm" variant="ghost" onClick={() => onEditField(editField)}>Edit on form</Button> : null}
      </div>
      <p className="mt-2 text-xs text-fg-muted">May answer: {decision.assignees.map(({ display_name }) => display_name).join(", ")}</p>
      {!compact ? <div className="mt-3 border-t border-border-subtle pt-2">
        <div className="flex min-w-0 flex-wrap gap-1">{decision.records.map((record) => record.record_id && record.record_id !== selfId && !inStep?.records.includes(record.record_id)
          ? <span key={record.id}>{recordLink(record.record_id)}</span> : null)}</div>
        <DecisionContext context={decision.context} representedRecords={decision.records.flatMap((record) => record.record_id ? [record.record_id] : [])} />
      </div> : null}
    </section>
  </Card></DecisionProvider>;
}
