import * as React from "react";
import * as v from "valibot";
import {
  Alert,
  SectionEyebrow,
  optionalTranslation,
  type RecordPeekReference,
  type UiTranslate,
} from "@angee/ui";

import { useWorkflowsT } from "../i18n";
import {
  DecisionReferenceAction,
  useInitialDecisionPeek,
  type WorkflowDecisionContentProps,
} from "./ApprovalTask";

export interface WorkflowDecisionHeader {
  eyebrow: React.ReactNode;
  title: React.ReactNode;
  description: React.ReactNode;
}

export interface WorkflowDecisionWarning {
  title?: React.ReactNode;
  description: React.ReactNode;
}

export interface WorkflowDecisionReference {
  key?: React.Key;
  label: string;
  reference: RecordPeekReference;
  kind?: "record" | "evidence";
}

export interface WorkflowDecisionScaffoldProps<Context> {
  props: WorkflowDecisionContentProps;
  schema: v.GenericSchema<unknown, Context>;
  header: (context: Context) => WorkflowDecisionHeader;
  warning?: (context: Context) => WorkflowDecisionWarning | null | undefined;
  references?: (context: Context) => readonly WorkflowDecisionReference[];
  initialPeek?: (context: Context) => WorkflowDecisionReference | RecordPeekReference | undefined;
  actionPickerPlacement?: "before-content" | "after-content" | false;
  children: (context: Context) => React.ReactNode;
}

/** Render a safe fallback while retaining framework-owned decision controls. */
export function DecisionContextUnavailable({
  props,
  placeActionPicker = false,
  title,
  description,
}: {
  props: WorkflowDecisionContentProps;
  placeActionPicker?: boolean;
  title?: React.ReactNode;
  description?: React.ReactNode;
}): React.ReactElement {
  const t = useWorkflowsT();
  return <div className="space-y-4">
    <Alert tone="warning" title={title ?? t("inbox.contextUnavailableTitle")}>
      {description ?? t("inbox.frozenContextUnavailable")}
    </Alert>
    {placeActionPicker ? props.actionPicker : null}
  </div>;
}

/** Own the common shell around domain-specific, frozen Decision review context. */
export function WorkflowDecisionScaffold<Context>({
  props,
  schema,
  header,
  warning,
  references,
  initialPeek,
  actionPickerPlacement = false,
  children,
}: WorkflowDecisionScaffoldProps<Context>): React.ReactElement {
  const parsed = React.useMemo(
    () => v.safeParse(schema, props.contextValues.review_context),
    [props.contextValues.review_context, schema],
  );
  const context = parsed.success ? parsed.output : undefined;
  const initial = context === undefined ? undefined : initialPeek?.(context);
  const initialReference = initial && "reference" in initial ? initial.reference : initial;
  useInitialDecisionPeek(props, initialReference);

  if (context === undefined) {
    return <DecisionContextUnavailable
      props={props}
      placeActionPicker={actionPickerPlacement !== false}
    />;
  }

  const heading = header(context);
  const warningContent = warning?.(context);
  const referenceActions = references?.(context) ?? [];
  return <section className="space-y-4">
    <header className="space-y-2 border-b border-border-subtle pb-3">
      <SectionEyebrow>{heading.eyebrow}</SectionEyebrow>
      <h2 className="text-xl font-semibold leading-tight text-fg md:text-2xl">{heading.title}</h2>
      <p className="max-w-prose text-13 leading-relaxed text-fg-muted">{heading.description}</p>
    </header>
    {warningContent ? <Alert tone="warning" title={warningContent.title}>
      {warningContent.description}
    </Alert> : null}
    {referenceActions.length ? <div className="flex flex-wrap gap-2">
      {referenceActions.map((action) => <DecisionReferenceAction
        key={action.key ?? `${action.reference.model}:${action.reference.id}`}
        label={action.label}
        reference={action.reference}
        open={action.kind === "evidence" ? props.openEvidence : props.openRecord}
      />)}
    </div> : null}
    {actionPickerPlacement === "before-content" ? props.actionPicker : null}
    {children(context)}
    {actionPickerPlacement === "after-content" ? props.actionPicker : null}
  </section>;
}

/** Drop rows whose retained value is absent while preserving renderable values. */
export function presentRows<T extends readonly [React.ReactNode, React.ReactNode]>(
  rows: readonly T[],
): T[] {
  return rows.filter(([, value]) => value !== "" && value !== null && value !== undefined);
}

/** Normalize retained scalar text without interpreting domain-specific objects. */
export function textValue(value: unknown): string {
  return typeof value === "string" || typeof value === "number" ? String(value).trim() : "";
}

/** Resolve a namespaced reason when translated, retaining its stable code otherwise. */
export function reasonLabel(
  t: UiTranslate,
  value: string | null | undefined,
  prefix = "decision.reasonCode",
): string {
  return value ? optionalTranslation(t, `${prefix}.${value}`) ?? value : "";
}

/** Return non-empty strings once, retaining declaration order. */
export function unique(values: readonly string[]): string[] {
  return [...new Set(values.filter(Boolean))];
}
