import * as React from "react";
import * as v from "valibot";
import {
  Alert,
  Collapsible,
  DetailSection,
  SectionEyebrow,
  type RecordPeekReference,
} from "@angee/ui";

import { useWorkflowsT } from "../i18n";
import {
  DecisionContextFields,
  DecisionReferenceAction,
  useInitialDecisionPeek,
  type WorkflowDecisionContentProps,
} from "./ApprovalTask";
import { decisionReviewContext } from "./DecisionContextWidgets";

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
  /** The retained value selected by the domain; never fetch live replacement facts. */
  context: unknown;
  schema: v.GenericSchema<unknown, Context>;
  header: (context: Context) => WorkflowDecisionHeader;
  warning?: (context: Context) => WorkflowDecisionWarning | null | undefined;
  references?: (context: Context) => readonly WorkflowDecisionReference[];
  /** Select an initial target from the same references rendered by the scaffold. */
  initialPeek?: (
    context: Context,
    references: readonly WorkflowDecisionReference[],
  ) => WorkflowDecisionReference | RecordPeekReference | undefined;
  actionPickerPlacement?: "before-content" | "after-content" | false;
  children: (context: Context) => React.ReactNode;
}

/** Presentation of the framework's retained facts and record references. */
export interface WorkflowDecisionContextDetails {
  title: React.ReactNode;
  description?: React.ReactNode;
  label: React.ReactNode;
}

export interface NativeWorkflowDecisionScaffoldProps {
  props: WorkflowDecisionContentProps;
  header: WorkflowDecisionHeader;
  contextDetails: WorkflowDecisionContextDetails;
  actionPickerPlacement?: "before-content" | "after-content" | false;
  children: React.ReactNode;
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
export function WorkflowDecisionScaffold(props: NativeWorkflowDecisionScaffoldProps): React.ReactElement;
export function WorkflowDecisionScaffold<Context>(props: WorkflowDecisionScaffoldProps<Context>): React.ReactElement;
export function WorkflowDecisionScaffold<Context>(
  props: WorkflowDecisionScaffoldProps<Context> | NativeWorkflowDecisionScaffoldProps,
): React.ReactElement {
  return "schema" in props
    ? <ParsedDecisionScaffold {...props} />
    : <NativeDecisionScaffold {...props} />;
}

function ParsedDecisionScaffold<Context>({
  props,
  context: retainedContext,
  schema,
  header,
  warning,
  references,
  initialPeek,
  actionPickerPlacement = false,
  children,
}: WorkflowDecisionScaffoldProps<Context>): React.ReactElement {
  const parsed = React.useMemo(
    () => v.safeParse(schema, retainedContext),
    [retainedContext, schema],
  );
  const context = parsed.success ? parsed.output : undefined;
  const contextReferences = context === undefined ? [] : references?.(context) ?? [];
  const initial = context === undefined ? undefined : initialPeek?.(context, contextReferences);
  const initialReference = initial && "reference" in initial ? initial.reference : initial;
  useInitialDecisionPeek(props, initialReference);

  if (context === undefined) {
    return <DecisionContextUnavailable
      props={props}
      placeActionPicker={actionPickerPlacement !== false}
    />;
  }

  return <DecisionScaffoldShell
    props={props}
    heading={header(context)}
    warning={warning?.(context)}
    references={contextReferences}
    actionPickerPlacement={actionPickerPlacement}
  >{children(context)}</DecisionScaffoldShell>;
}

function NativeDecisionScaffold({
  props,
  header,
  contextDetails,
  actionPickerPlacement,
  children,
}: NativeWorkflowDecisionScaffoldProps): React.ReactElement {
  const context = React.useMemo(
    () => decisionReviewContext(props.contextValues),
    [props.contextValues],
  );
  const references = context?.references;
  useInitialDecisionPeek(props, Array.isArray(references) ? references[0] : references);

  const details = context === undefined ? <DecisionContextUnavailable props={props} /> : (
    <DetailSection title={contextDetails.title}>
      <div className="space-y-3">
        {contextDetails.description ? <p className="text-13 text-fg-muted">{contextDetails.description}</p> : null}
        <Collapsible variant="section">
          <Collapsible.Trigger><Collapsible.Icon />{contextDetails.label}</Collapsible.Trigger>
          <Collapsible.Panel><div className="pt-2">
            <DecisionContextFields fields={props.contextFields} values={props.contextValues} />
          </div></Collapsible.Panel>
        </Collapsible>
      </div>
    </DetailSection>
  );
  return <DecisionScaffoldShell
    props={props}
    heading={header}
    contextDetails={details}
    actionPickerPlacement={actionPickerPlacement}
  >{children}</DecisionScaffoldShell>;
}

function DecisionScaffoldShell({
  props,
  heading,
  warning,
  references = [],
  contextDetails,
  actionPickerPlacement = false,
  children,
}: {
  props: WorkflowDecisionContentProps;
  heading: WorkflowDecisionHeader;
  warning?: WorkflowDecisionWarning | null;
  references?: readonly WorkflowDecisionReference[];
  contextDetails?: React.ReactNode;
  actionPickerPlacement?: "before-content" | "after-content" | false;
  children: React.ReactNode;
}): React.ReactElement {
  return <section className="space-y-4">
    <header className="space-y-2 border-b border-border-subtle pb-3">
      <SectionEyebrow>{heading.eyebrow}</SectionEyebrow>
      <h2 className="text-xl font-semibold leading-tight text-fg md:text-2xl">{heading.title}</h2>
      <p className="max-w-prose text-13 leading-relaxed text-fg-muted">{heading.description}</p>
    </header>
    {warning ? <Alert tone="warning" title={warning.title}>
      {warning.description}
    </Alert> : null}
    {references.length ? <div className="flex flex-wrap gap-2">
      {references.map((action) => <DecisionReferenceAction
        key={action.key ?? `${action.reference.model}:${action.reference.id}`}
        label={action.label}
        reference={action.reference}
        open={action.kind === "evidence" ? props.openEvidence : props.openRecord}
      />)}
    </div> : null}
    {contextDetails}
    {actionPickerPlacement === "before-content" ? props.actionPicker : null}
    {children}
    {actionPickerPlacement === "after-content" ? props.actionPicker : null}
  </section>;
}

/** Normalize retained scalar text without interpreting domain-specific objects. */
export function textValue(value: unknown): string {
  return typeof value === "string" || typeof value === "number" ? String(value).trim() : "";
}
