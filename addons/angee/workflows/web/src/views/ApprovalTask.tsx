import * as React from "react";
import { useAuthoredMutation, type DocumentVariables } from "@angee/refine";
import {
  Badge, Button, Collapsible, ErrorBanner, FieldDescription, FieldLabel, FieldRoot,
  Glyph, LabeledDescriptorField, LazyBoundary, Textarea, formSpecInitialValues,
  useDottedPathFieldErrors, useFormSpecFields, validationErrorMap,
  type DottedPathFieldErrorMap,
} from "@angee/ui";
import { DecideWorkflowDecisionDocument, type PendingWorkflowDecision } from "../documents.public";
import { useWorkflowsT } from "../i18n";
import { JsonBlock } from "./JsonBlock";

const DECISION_MODEL = "workflows.Decision";
type ApprovalVerdict = DocumentVariables<typeof DecideWorkflowDecisionDocument>["verdict"];
export interface ApprovalTaskProps {
  approval: PendingWorkflowDecision;
  onBack?: () => void;
  onResolved: () => void;
}

/** The workflow-owned approval task, shared by approval and run surfaces. */
export function ApprovalTask({ approval, onBack, onResolved }: ApprovalTaskProps): React.ReactElement {
  const t = useWorkflowsT();
  return (
    <aside className="h-full min-h-0 overflow-auto bg-sheet-1 p-4">
      <div className="space-y-4">
        {onBack ? (
          <Button type="button" variant="ghost" onClick={onBack}>
            <Glyph name="chevron-left" />
            {t("inbox.back")}
          </Button>
        ) : null}
        <div className="space-y-2">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-fg">{approval.step_name || approval.action}</h2>
              <p className="mt-1 text-13 text-fg-muted">{approval.workflow_name || t("inbox.workflowFallback")}</p>
            </div>
            <Badge tone="warning">{approval.verdict}</Badge>
          </div>
        </div>
        {approval.decision_schema == null ? (
          <JsonApprovalResolution approval={approval} onResolved={onResolved} />
        ) : (
          <LazyBoundary pending={null} fallback={<ErrorBanner description={t("inbox.invalidFormSpec")} />} resetKey={approval.id}>
            <FormSpecApprovalResolution approval={approval} onResolved={onResolved} />
          </LazyBoundary>
        )}
        <Collapsible variant="section">
          <Collapsible.Trigger><Collapsible.Icon />{t("inbox.sourceData")}</Collapsible.Trigger>
          <Collapsible.Panel>
            <div className="mb-2 text-xs text-fg-muted">
              {t("inbox.sourceMetadata")}: {approval.action} · {approval.priority}
            </div>
            <JsonBlock value={approval.payload} />
          </Collapsible.Panel>
        </Collapsible>
      </div>
    </aside>
  );
}

function FormSpecApprovalResolution({ approval, onResolved }: { approval: PendingWorkflowDecision; onResolved: () => void }): React.ReactElement {
  const t = useWorkflowsT();
  const fields = useFormSpecFields(approval.decision_schema);
  const [values, setValues] = React.useState<Record<string, unknown>>(() => formSpecInitialValues(fields, approval.payload));
  const fieldNames = React.useMemo(() => fields.map((field) => field.name), [fields]);
  const validationErrors = useDottedPathFieldErrors(fieldNames);
  const [error, setError] = React.useState<string | null>(null);
  const resolution = useApprovalResolver(onResolved);
  async function resolve(verdict: ApprovalVerdict): Promise<void> {
    setError(null); validationErrors.clear();
    try {
      validationErrors.replace(await resolution.resolve(approval.id, verdict, values));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("inbox.actionFailed"));
    }
  }
  return (
    <section className="space-y-3">
      <h3 className="text-xs font-semibold text-fg-muted">{t("inbox.yourDecision")}</h3>
      {fields.map((field) => (
        <LabeledDescriptorField key={field.name} field={field} value={values[field.name]}
          readOnly={field.readOnly || resolution.fetching} messages={validationErrors.messagesFor(field.name)}
          onChange={(value) => { validationErrors.clearField(field.name); setValues((current) => ({ ...current, [field.name]: value })); }} />
      ))}
      <ErrorBanner description={error ?? resolution.error?.message ?? validationErrors.formSummary} />
      <ApprovalVerdictButtons fetching={resolution.fetching} onResolve={resolve} />
    </section>
  );
}

function JsonApprovalResolution({ approval, onResolved }: { approval: PendingWorkflowDecision; onResolved: () => void }): React.ReactElement {
  const t = useWorkflowsT();
  const payloadId = React.useId();
  const [payload, setPayload] = React.useState("{}");
  const validationErrors = useDottedPathFieldErrors();
  const [error, setError] = React.useState<string | null>(null);
  const resolution = useApprovalResolver(onResolved);
  const validationError = validationErrors.formSummary;
  async function resolve(verdict: ApprovalVerdict): Promise<void> {
    setError(null); validationErrors.clear();
    let parsed: unknown;
    try { parsed = parseJsonPayload(payload, t("json.invalid")); }
    catch (cause) { setError(cause instanceof Error ? cause.message : t("inbox.actionFailed")); return; }
    try { validationErrors.replace(await resolution.resolve(approval.id, verdict, parsed)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : t("inbox.actionFailed")); }
  }
  return (
    <section className="space-y-3">
      <FieldRoot invalid={Boolean(error || validationError)}>
        <FieldLabel htmlFor={payloadId}>{t("inbox.resolution")}</FieldLabel>
        <Textarea id={payloadId} rows={8} value={payload} invalid={Boolean(error || validationError)}
          onChange={(event) => { validationErrors.clear(); setPayload(event.target.value); }} />
        <FieldDescription>{t("json.label")}</FieldDescription>
      </FieldRoot>
      <ErrorBanner description={error ?? resolution.error?.message ?? validationError} />
      <ApprovalVerdictButtons fetching={resolution.fetching} onResolve={resolve} />
    </section>
  );
}

function ApprovalVerdictButtons({ fetching, onResolve }: { fetching: boolean; onResolve: (verdict: ApprovalVerdict) => void | Promise<void> }): React.ReactElement {
  const t = useWorkflowsT();
  return <div className="flex flex-wrap justify-end gap-2">
    <Button type="button" variant="ghost" loading={fetching} onClick={() => void onResolve("ESCALATE")}><Glyph name="workflow-escalate" />{t("inbox.escalate")}</Button>
    <Button type="button" variant="secondary" loading={fetching} onClick={() => void onResolve("REJECT")}><Glyph name="workflow-reject" />{t("inbox.reject")}</Button>
    <Button type="button" variant="primary" loading={fetching} onClick={() => void onResolve("COMPLETE")}><Glyph name="workflow-approve" />{t("inbox.complete")}</Button>
  </div>;
}

function useApprovalResolver(onResolved: () => void): {
  resolve: (approval: string, verdict: ApprovalVerdict, payload: unknown) => Promise<DottedPathFieldErrorMap>;
  fetching: boolean; error: Error | null;
} {
  const t = useWorkflowsT();
  const [decide, state] = useAuthoredMutation(DecideWorkflowDecisionDocument, {
    dataProviderName: "public", invalidateModels: [DECISION_MODEL],
    shouldInvalidate: (data) => data?.decide.validation_errors == null,
  });
  const resolve = React.useCallback(async (approval: string, verdict: ApprovalVerdict, payload: unknown): Promise<DottedPathFieldErrorMap> => {
    const data = await decide({ decision: approval, verdict, payload });
    const wireErrors = data?.decide.validation_errors;
    const parsedErrors = validationErrorMap(wireErrors);
    if (wireErrors != null && parsedErrors === null) throw new Error(t("inbox.invalidValidationErrors"));
    const errors = parsedErrors ?? {};
    if (Object.keys(errors).length === 0) onResolved();
    return errors;
  }, [decide, onResolved, t]);
  return { resolve, fetching: state.fetching, error: state.error };
}

function parseJsonPayload(value: string, invalidMessage: string): unknown {
  const trimmed = value.trim();
  if (!trimmed) return {};
  try { return JSON.parse(trimmed) as unknown; }
  catch (error) { throw new Error(error instanceof Error && error.message ? `${invalidMessage}: ${error.message}` : invalidMessage); }
}
