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
import { definitionValueEqual } from "./workflow-definition-state";

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
          key={eventModel(binding.value)}
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
  const queriedResult = query.data?.workflow_event_condition_draft;
  const result = queriedResult && definitionValueEqual(queriedResult.condition, conditionInput) ? queriedResult : undefined;
  const catalogueRef = React.useRef<{ model: string; fields: readonly ConditionField[] } | null>(null);
  if (result) catalogueRef.current = { model, fields: result.fields };
  const fields = result?.fields ?? (catalogueRef.current?.model === model ? catalogueRef.current.fields : []);
  const [blankClauses, setBlankClauses] = React.useState<Array<{ id: string; clause: Clause }>>([]);
  const [clientErrors, setClientErrors] = React.useState<Record<string, string>>({});
  const clientErrorsRef = React.useRef<Record<string, string>>({});
  const serverErrorsRef = React.useRef<readonly string[]>([]);
  const locallyWritten = React.useRef<string | null>(null);
  serverErrorsRef.current = result?.errors ?? [];

  React.useEffect(() => context.form.registerFieldValidation("config", () => (
    Object.values(clientErrorsRef.current)[0] ?? serverErrorsRef.current[0]
  )), [context.form.registerFieldValidation]);

  React.useEffect(() => {
    if (locallyWritten.current === conditionKey) {
      locallyWritten.current = null;
      return;
    }
    setBlankClauses([]);
    setClientErrors({});
    clientErrorsRef.current = {};
    context.form.form.clearErrors("config");
  }, [conditionKey]);
  React.useEffect(() => {
    locallyWritten.current = null;
    setBlankClauses([]);
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
  const clauses = conditionClauses(condition, fields);
  const write = (nextCondition: Record<string, unknown>) => {
    locallyWritten.current = JSON.stringify(nextCondition);
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
  const replaceClause = (next: Clause, oldKey: string, nextKey: string) => {
    if (nextKey !== oldKey && Object.hasOwn(condition, nextKey)) {
      setClientError(oldKey, t("triggers.conditionCollision"));
      return;
    }
    setClientError(oldKey);
    const nextCondition = { ...condition };
    if (oldKey !== nextKey) delete nextCondition[oldKey];
    nextCondition[nextKey] = next.value;
    write(nextCondition);
  };

  return (
    <section aria-label={t("triggers.conditions")} className="grid gap-3">
      <div>
        <strong>{t("triggers.conditions")}</strong>
        <p className="text-13 text-fg-muted">{t("triggers.conditionsDescription")}</p>
      </div>
      {query.error ? <ErrorBanner description={t("triggers.conditionError")} /> : null}
      {result?.errors.map((message) => <ErrorBanner key={message} description={message} />)}
    {clauses.map((clause) => {
      const oldKey = clause.source_key ?? canonicalKey(fields, clause);
      return (
        <ClauseEditor
        key={oldKey}
        clause={clause}
        fields={fields}
        readOnly={context.form.formReadOnly}
        messages={clientErrors[oldKey] ? [clientErrors[oldKey]] : []}
        onChange={(next, nextKey) => replaceClause(next, oldKey, nextKey)}
        onRemove={() => {
          const nextCondition = { ...condition };
          delete nextCondition[oldKey];
          setClientError(oldKey);
          write(nextCondition);
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
    {blankClauses.map(({ id: blankId, clause }) => {
      return <ClauseEditor
        key={blankId}
        clause={clause}
        fields={fields}
        readOnly={context.form.formReadOnly}
        messages={clientErrors[blankId] ? [clientErrors[blankId]] : []}
        onChange={(next, nextKey, valid) => {
          if (valid) {
            if (Object.hasOwn(condition, nextKey)) {
              setClientError(blankId, t("triggers.conditionCollision"));
              return;
            }
            setBlankClauses((current) => current.filter(({ id }) => id !== blankId));
            setClientError(blankId);
            write({ ...condition, [nextKey]: next.value });
          } else {
            setBlankClauses((current) => current.map((entry) => entry.id === blankId ? { ...entry, clause: next } : entry));
            setClientError(blankId, t("triggers.conditionInvalidValue"));
          }
        }}
        onRemove={() => {
          setBlankClauses((current) => current.filter(({ id }) => id !== blankId));
          setClientError(blankId);
          onCommit();
        }}
        onValidate={() => undefined}
        onInvalidate={() => setClientError(blankId, t("triggers.conditionInvalidValue"))}
        onCommit={onCommit}
      />;
    })}
      {!context.form.formReadOnly && fields.length ? (
        <Button type="button" size="sm" variant="secondary" onClick={() => {
          const available = fields
            .flatMap((field) => field.lookups.map((lookup) => ({ field, lookup })))
            .find(({ lookup }) => !Object.hasOwn(condition, lookup.key) && !blankClauses.some(({ clause }) => clause.source_key === lookup.key));
          if (!available) {
            setClientError("root", t("triggers.conditionCollision"));
            return;
          }
          const { field, lookup } = available;
          const clause: Clause = {
            field: field.name,
            lookup: lookup.name,
            value: "",
            source_key: lookup.key,
          };
          const id = globalThis.crypto.randomUUID();
          setBlankClauses((current) => [...current, { id, clause }]);
          setClientError(id, t("triggers.conditionInvalidValue"));
        }}>
          {t("triggers.addCondition")}
        </Button>
      ) : null}
      {hasOpaqueConditions(condition, fields) ? (
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
  onChange: (clause: Clause, key: string, valid: boolean) => void;
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
                false,
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
                false,
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
            const valid = structuredFieldErrorPaths(valueField, value, true).length === 0;
            onChange({ ...clause, value }, currentKey, valid);
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
function conditionClauses(condition: Record<string, unknown>, fields: readonly ConditionField[]): Clause[] {
  const lookups = new Map(fields.flatMap((field) => field.lookups.map((lookup) => [lookup.key, { field, lookup }] as const)));
  return Object.entries(condition).flatMap(([sourceKey, value]) => {
    const declared = lookups.get(sourceKey);
    return declared ? [{ field: declared.field.name, lookup: declared.lookup.name, value, source_key: sourceKey }] : [];
  });
}
function hasOpaqueConditions(condition: Record<string, unknown>, fields: readonly ConditionField[]): boolean {
  const declared = new Set(fields.flatMap((field) => field.lookups.map((lookup) => lookup.key)));
  return Object.keys(condition).some((key) => !declared.has(key));
}
function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function eventModel(value: unknown): string {
  return isObject(value) && typeof value.model === "string" ? value.model : "";
}
