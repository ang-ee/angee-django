import { useEffect, useRef, useState, type ReactElement, type ReactNode } from "react";
import * as v from "valibot";
import type { ActionFieldName } from "@angee/gql/console/actions";
import { holdsPermission, useSchemaFieldMetadata, modelMetadataForLabel } from "@angee/metadata";
import {
  Button, Card, Checkbox, ErrorBanner, MetaGrid, RadioGroup, RecordReference,
  RelativeTime, StatusIcon, cn, radioGroupVariants,
  actionOutcomeSubmitResult, formSubmitError, titleCase, useActionOutcomeMutation, useRevealedRecordField, useActiveRecordForm, useRecordPeek,
} from "@angee/ui";
import { DecisionContext, DecisionContextSchema, FactValue } from "./DecisionContext";
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
  actions?: ReactNode;
}

/** One card in the inbox, a record timeline and a record selection. */
export function DecisionCard({ decision, selfId, highlighted, compact, inStep, onEditField, onAnswered, actions }: DecisionCardProps): ReactElement {
  const t = useDecisionsT();
  const ref = useRef<HTMLElement>(null);
  const openRecord = useRecordPeek();
  const metadata = useSchemaFieldMetadata();
  const revealed = useRevealedRecordField();
  const form = useActiveRecordForm();
  const fieldLabel = (id: string, field: string, model?: string) => (form?.id === id ? form.fieldLabel?.(field) : undefined)
    ?? modelMetadataForLabel(metadata, model ?? decision.records.find((record) => record.record_id === id)?.record_model ?? "")?.fields[field]?.label
    ?? titleCase(field);
  const proposal = v.safeParse(ProposalSchema, decision.proposal);
  highlighted ||= proposal.success && revealed != null && revealed.id === selfId && proposal.output.alternatives.some((alternative) =>
    Object.hasOwn(alternative.actions[selfId ?? ""]?.fields ?? {}, revealed.field));
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
    return <p className="text-xs text-fg-muted">{decision.kind_label}{" · "}
      {!withdrawn && decision.answered_by ? <><span>{decision.answered_by.display_name}</span>{" · "}</> : null}<span>{withdrawn
      ? t("decision.withdrawn", { name: decision.answered_by?.display_name ?? t("decision.operator") })
      : t("decision.chose", { labels: decision.verdict_label })}</span></p>;
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
            const reference = references.find((reference) => reference.id === operation.set);
            return <FactValue value={operation.set} relationModel={facts?.relationModelLabel ?? reference?.model} label={reference?.label} options={facts?.values}
              widget={facts?.widget} row={context.success ? context.output.facts.find((fact) => Object.keys(fact.row).length)?.row : undefined}
              emptyLabel={t("decision.none")} />;
          })(),
        }))} />
      </span>)}</span>;
  const alternatives = proposal.success ? proposal.output.alternatives : [];
  const editField = alternatives.flatMap((alternative) => Object.keys(alternative.actions[selfId ?? ""]?.fields ?? {}))[0];
  return <DecisionProvider value={{ decision }}><Card asChild className={cn("min-w-0 p-3 shadow-none", highlighted && "border-brand ring-2 ring-brand/30")}>
    <section ref={ref} aria-label={decision.kind_label} data-decision={decision.id}>
      <div className="flex min-w-0 items-start gap-2"><StatusIcon tone="warning" size="sm" icon="help" className="mt-0.5" />
        <div className="min-w-0 flex-1"><h2 className="text-13 font-semibold leading-5 text-fg [overflow-wrap:anywhere]">{decision.kind_label}</h2>
          {!inStep ? <div className="mt-0.5 text-xs text-fg-muted"><DecisionOriginOutlet />
            {decision.requester ? t("decision.requestedBy", { name: decision.requester.display_name }) : null}{" · "}<RelativeTime value={decision.created_at} /></div> : null}
        </div>
      </div>
      <DecisionContext context={decision.context} reasonOnly />
      {proposal.success ? <>
        <div className="mt-3">{proposal.output.multiple ? <div role="group" aria-label={t("decision.multipleAlternatives")} className="grid gap-1.5">
          {alternatives.map((alternative) => {
            const selected = chosen.includes(alternative.key); const styles = radioGroupVariants();
            const id = `${decision.id}-${alternative.key}`;
            return <div key={alternative.key} className={cn(styles.item(), "border", selected ? "border-brand bg-brand-tint" : "border-border-subtle")}>
              <Checkbox id={id} aria-label={alternative.label} checked={selected} disabled={busy || !canAnswer} onCheckedChange={(on) => setChosen((current) =>
                on ? [...new Set([...current, alternative.key])] : current.filter((key) => key !== alternative.key))} />
              <span className={styles.text()}><label htmlFor={id} className={styles.label()}>{alternative.label}</label>{changes(alternative)}</span>
            </div>;
          })}</div> : <RadioGroup aria-label={t("decision.alternatives")} className="gap-1.5" value={chosen[0] ?? null}
            disabled={busy || !canAnswer} onValueChange={(value: unknown) => setChosen([String(value)])}>
            {alternatives.map((alternative) => <RadioGroup.Item key={alternative.key} value={alternative.key} label={alternative.label}
              className={cn("border", chosen[0] === alternative.key ? "border-brand bg-brand-tint" : "border-border-subtle")}
              description={changes(alternative)} />)}
          </RadioGroup>}</div>
      </> : <ErrorBanner description={t("decision.invalidProposal")} />}
      {error ? <ErrorBanner description={error} /> : null}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {canAnswer && proposal.success ? <Button size="sm" variant="primary" disabled={busy || !chosen.length} onClick={() => void submit()}>{t("decision.submit")}</Button> : null}
        {onEditField && editField ? <Button size="sm" variant="ghost" onClick={() => onEditField(editField)}>{t("decision.editOnForm")}</Button> : null}
        {actions}
      </div>
      <p className="mt-2 text-xs text-fg-muted">{t("decision.mayAnswer", { names: decision.assignees.map(({ display_name }) => display_name).join(", ") })}</p>
      {!compact ? <div className="mt-3 border-t border-border-subtle pt-2">
        <div className="flex min-w-0 flex-wrap gap-1">{decision.records.map((record) => record.record_id && record.record_id !== selfId && !inStep?.records.includes(record.record_id)
          ? <span key={record.id}>{recordLink(record.record_id)}</span> : null)}</div>
        <DecisionContext context={decision.context} representedRecords={decision.records.flatMap((record) => record.record_id ? [record.record_id] : [])} />
      </div> : null}
    </section>
  </Card></DecisionProvider>;
}
