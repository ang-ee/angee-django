import * as React from "react";
import type { DocumentType } from "@angee/gql/console";
import { useAuthoredQuery } from "@angee/refine";
import {
  BoundFormValue,
  Button,
  ErrorBanner,
  FieldDescriptorControl,
  FieldLabel,
  FieldRoot,
  LabeledDescriptorField,
  structuredFieldErrorPaths,
  useFormSpecFields,
  useFormViewValues,
  type FormSpecFieldDescriptor,
  type RecordToolbarContext,
} from "@angee/ui";

import { WorkflowEventConditionDraftDocument } from "../documents.console";
import { useWorkflowsT } from "../i18n";

type Result = DocumentType<typeof WorkflowEventConditionDraftDocument>["workflow_event_condition_draft"];
type Clause = Result["clauses"][number];
type ConditionField = Result["fields"][number];
type Lookup = ConditionField["lookups"][number];
type Config = Record<string, unknown> & { model?: unknown; condition?: unknown };

export function WorkflowEventConditionEditor({
  context,
}: {
  context: RecordToolbarContext;
}): React.ReactElement | null {
  const values = useFormViewValues(context.form);
  if (String(values.kind ?? "").toLowerCase() !== "event") return null;
  return (
    <BoundFormValue form={context.form} name="config">
      {(binding) => (
        <EventConditionDraft
          context={context}
          config={isObject(binding.value) ? binding.value : {}}
          onChange={binding.onChange}
          onCommit={binding.onCommit}
        />
      )}
    </BoundFormValue>
  );
}

function EventConditionDraft({
  context,
  config,
  onChange,
  onCommit,
}: {
  context: RecordToolbarContext;
  config: Config;
  onChange: (value: Config) => void;
  onCommit: () => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const model = typeof config.model === "string" ? config.model : "";
  const condition = isObject(config.condition) ? config.condition : {};
  const conditionInput = Object.hasOwn(config, "condition") ? config.condition : {};
  const conditionKey = JSON.stringify(conditionInput);
  const query = useAuthoredQuery(
    WorkflowEventConditionDraftDocument,
    { model, condition: conditionInput, clauses: null, opaque: null },
    { enabled: Boolean(model) },
  );
  const result = query.data?.workflow_event_condition_draft;
  const resultKey = JSON.stringify(result ?? null);
  const [localClauses, setLocalClauses] = React.useState<Clause[] | null>(null);
  const [clientErrors, setClientErrors] = React.useState<Record<string, string>>({});
  const clientErrorsRef = React.useRef<Record<string, string>>({});
  const serverErrorsRef = React.useRef<readonly string[]>([]);
  const locallyWritten = React.useRef<string | null>(null);
  serverErrorsRef.current = result?.errors ?? [];

  React.useEffect(() => context.form.registerFieldValidation("config", () => (
    Object.values(clientErrorsRef.current)[0] ?? serverErrorsRef.current[0]
  )), [context.form.registerFieldValidation]);

  React.useEffect(() => {
    if (result && locallyWritten.current !== conditionKey) setLocalClauses([...result.clauses]);
  }, [conditionKey, resultKey]);
  React.useEffect(() => {
    locallyWritten.current = null;
    setLocalClauses(null);
    setClientErrors({});
    clientErrorsRef.current = {};
    context.form.form.clearErrors("config");
  }, [model]);

  if (!model) {
    return (
      <section aria-label={t("triggers.conditions")}>
        <p>{t("triggers.choosePublisher")}</p>
      </section>
    );
  }
  const clauses = localClauses ?? result?.clauses ?? [];
  const write = (nextCondition: Record<string, unknown>, nextClauses: Clause[]) => {
    locallyWritten.current = JSON.stringify(nextCondition);
    setLocalClauses(nextClauses);
    onChange({ ...config, condition: nextCondition });
  };
  const setClientError = (key: string, message?: string) => {
    const next = { ...clientErrorsRef.current };
    if (message) next[key] = message;
    else delete next[key];
    clientErrorsRef.current = next;
    setClientErrors(next);
    if (Object.keys(next).length) {
      context.form.form.setError("config", {
        type: "condition",
        message: Object.values(next)[0],
      });
    } else context.form.form.clearErrors("config");
  };
  const replaceClause = (index: number, next: Clause, oldKey: string, nextKey: string) => {
    if (nextKey !== oldKey && Object.hasOwn(condition, nextKey)) {
      setClientError(oldKey, t("triggers.conditionCollision"));
      return;
    }
    setClientError(oldKey);
    const nextCondition = { ...condition };
    if (oldKey !== nextKey) delete nextCondition[oldKey];
    nextCondition[nextKey] = next.value;
    write(nextCondition, replaceAt(clauses, index, next));
  };

  return (
    <section aria-label={t("triggers.conditions")} className="grid gap-3">
      <div>
        <strong>{t("triggers.conditions")}</strong>
        <p className="text-13 text-fg-muted">{t("triggers.conditionsDescription")}</p>
      </div>
      {query.error ? <ErrorBanner description={t("triggers.conditionError")} /> : null}
      {result?.errors.map((message) => <ErrorBanner key={message} description={message} />)}
    {clauses.map((clause, index) => {
      const oldKey = clause.source_key ?? canonicalKey(result?.fields, clause);
      return (
        <ClauseEditor
        key={`${oldKey}:${index}`}
        clause={clause}
        fields={result?.fields ?? []}
        readOnly={context.form.formReadOnly}
        messages={clientErrors[oldKey] ? [clientErrors[oldKey]] : []}
        onChange={(next, nextKey) => replaceClause(index, next, oldKey, nextKey)}
        onRemove={() => {
          const nextCondition = { ...condition };
          delete nextCondition[oldKey];
          setClientError(oldKey);
          write(nextCondition, clauses.filter((_, candidate) => candidate !== index));
          onCommit();
        }}
        onValidate={(key, field, value) => {
          if (structuredFieldErrorPaths(field, value, true).length) {
            setClientError(key, t("triggers.conditionInvalidValue"));
          } else setClientError(key);
        }}
        onInvalidate={(key) => setClientError(key, t("triggers.conditionInvalidValue"))}
        onCommit={onCommit}
        />
      );
    })}
      {!context.form.formReadOnly && result?.fields.length ? (
        <Button type="button" size="sm" variant="secondary" onClick={() => {
          const available = result.fields
            .flatMap((field) => field.lookups.map((lookup) => ({ field, lookup })))
            .find(({ lookup }) => !Object.hasOwn(condition, lookup.key));
          if (!available) {
            setClientError("root", t("triggers.conditionCollision"));
            return;
          }
          const { field, lookup } = available;
          const clause = {
            field: field.name,
            lookup: lookup.name,
            value: "",
            source_key: lookup.key,
          };
          setClientError(lookup.key, t("triggers.conditionInvalidValue"));
          write({ ...condition, [lookup.key]: "" }, [...clauses, clause]);
        }}>
          {t("triggers.addCondition")}
        </Button>
      ) : null}
      {result && isObject(result.opaque) && Object.keys(result.opaque).length ? (
        <p className="text-13 text-fg-muted">{t("triggers.opaqueConditions")}</p>
      ) : null}
    </section>
  );
}

function ClauseEditor({
  clause,
  fields,
  readOnly,
  messages,
  onChange,
  onRemove,
  onValidate,
  onInvalidate,
  onCommit,
}: {
  clause: Clause;
  fields: readonly ConditionField[];
  readOnly: boolean;
  messages: readonly string[];
  onChange: (clause: Clause, key: string) => void;
  onRemove: () => void;
  onValidate: (key: string, field: FormSpecFieldDescriptor, value: unknown) => void;
  onInvalidate: (key: string) => void;
  onCommit: () => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const field = fields.find((candidate) => candidate.name === clause.field) ?? fields[0];
  const lookup = field?.lookups.find((candidate) => candidate.name === clause.lookup)
    ?? field?.lookups[0];
  const valueField = useConditionValueField(lookup);
  const currentKey = clause.source_key ?? lookup?.key ?? clause.field;
  return (
    <div className="grid gap-3 rounded-6 border border-border-subtle p-3 sm:grid-cols-3">
      <FieldRoot>
        <FieldLabel>{t("triggers.conditionField")}</FieldLabel>
        <FieldDescriptorControl
          field={{
            name: "field",
            label: t("triggers.conditionField"),
            widget: "select",
            options: fields.map((candidate) => ({
              value: candidate.name,
              label: candidate.label,
            })),
          }}
          value={clause.field}
          readOnly={readOnly}
          onChange={(next) => {
            const selected = fields.find((candidate) => candidate.name === next);
            const selectedLookup = selected?.lookups[0];
            if (selected && selectedLookup) {
              onInvalidate(selectedLookup.key);
              onChange(
                {
                  ...clause,
                  field: selected.name,
                  lookup: selectedLookup.name,
                  source_key: selectedLookup.key,
                },
                selectedLookup.key,
              );
            }
          }}
        />
      </FieldRoot>
      <FieldRoot>
        <FieldLabel>{t("triggers.conditionOperator")}</FieldLabel>
        <FieldDescriptorControl
          field={{
            name: "lookup",
            label: t("triggers.conditionOperator"),
            widget: "select",
            options: (field?.lookups ?? []).map((candidate) => ({
              value: candidate.name,
              label: candidate.label,
            })),
          }}
          value={clause.lookup}
          readOnly={readOnly}
          onChange={(next) => {
            const selected = field?.lookups.find((candidate) => candidate.name === next);
            if (selected) {
              onInvalidate(selected.key);
              onChange(
                { ...clause, lookup: selected.name, source_key: selected.key },
                selected.key,
              );
            }
          }}
        />
      </FieldRoot>
      {valueField ? (
        <LabeledDescriptorField
          field={{ ...valueField, name: currentKey, label: t("triggers.conditionValue") }}
          value={clause.value}
          readOnly={readOnly}
          messages={messages}
          onChange={(value) => {
            onChange({ ...clause, value }, currentKey);
            onValidate(currentKey, valueField, value);
          }}
          onCommit={onCommit}
        />
      ) : null}
      {!readOnly ? (
        <Button type="button" size="sm" variant="ghost" onClick={onRemove}>
          {t("triggers.removeCondition")}
        </Button>
      ) : null}
    </div>
  );
}

function useConditionValueField(lookup: Lookup | undefined): FormSpecFieldDescriptor | undefined {
  const spec = React.useMemo(() => ({
    type: "object",
    properties: lookup ? { value: lookup.value_schema } : {},
    required: lookup ? ["value"] : [],
  }), [lookup]);
  return useFormSpecFields(spec)[0];
}
function canonicalKey(fields: readonly ConditionField[] | undefined, clause: Clause): string {
  return fields
    ?.find(({ name }) => name === clause.field)
    ?.lookups.find(({ name }) => name === clause.lookup)?.key ?? clause.field;
}
function replaceAt<T>(values: readonly T[], index: number, value: T): T[] {
  const next = [...values];
  next[index] = value;
  return next;
}
function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
