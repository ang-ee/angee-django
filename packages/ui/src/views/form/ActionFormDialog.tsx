import * as React from "react";
import { createPortal } from "react-dom";
import { Controller, useWatch, type Control } from "react-hook-form";
import { rowPublicId, useModelMetadata } from "@angee/metadata";
import type { ActionOutcome } from "@angee/refine";

import { DialogForm } from "../../fragments/DialogForm";
import { ErrorBanner } from "../../fragments/ErrorBanner";
import { errorMessage } from "../../feedback";
import { Button } from "../../ui/button";
import { FieldDescription, FieldLabel, FieldRoot } from "../../ui/field";
import { useUiT } from "../../i18n";
import { titleCase } from "../../lib/titleCase";
import { relationIdList, relationValueId } from "../../widgets/types";
import { FieldDescriptorControl } from "./field-descriptor-control";
import { useRuntimeViewAs } from "../../runtime";
import {
  emptyDialogValue,
  emptyValueForField,
  mutationDialogValueCodecs,
} from "./MutationDialog";
import { relationFieldInfoForResource } from "../resource/model-metadata-defaults";
import { useRecordChromeContextMaybe } from "../resource/record-chrome-context";
import { RelationFieldWidget } from "../relation/RelationFieldWidget";
import { RelationMultiFieldWidget } from "../relation/RelationMultiFieldWidget";
import { useActionForm } from "./use-action-form";
import { ActionFormProvider } from "./ActionFormProvider";
import { DescriptorFieldList } from "./DescriptorFieldList";
import { actionOutcomeSubmitResult } from "./validation-errors";
import { fieldErrorMessages } from "./form-view-model";
import type { ActionArg, ActionArgs, ActionDescriptor, ActionFormContext, ActionFormDefinition } from "../page";

export interface ActionFormDialogProps {
  /** The action being collected — must declare `args` and `submit`. */
  action: ActionDescriptor;
  /** The invoking record/selection a `relationList` arg prefills from. */
  context: ActionFormContext;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called once with the successful (`ok`) outcome — e.g. to reload or follow its record. */
  onSucceeded?: (outcome: ActionOutcome) => void;
  /**
   * Render the fields in a saved record's page flow and the submit in its
   * record toolbar instead of a dialog; success does not close it.
   */
  inline?: boolean;
}

type ArgValues = Record<string, unknown>;

/**
 * The typed-args action form: a dialog that collects an action's declared
 * `args` — scalars, a single relation picker, or a relation list prefilled from
 * the invoking selection/record — then fires the authored mutation via the
 * action's `submit`. It composes the shared owners (`DialogForm`,
 * `FieldDescriptorControl`, `RelationFieldWidget`/`RelationMultiFieldWidget`)
 * with react-hook-form for collection, and `useActionForm` for the submit
 * lifecycle; no hand-rolled `<form>`.
 *
 * On an `ok=false` outcome it binds the in-band `validationErrors` to their args
 * and stays open; it closes only on `ok=true`, toasting the success `message`.
 * A thrown (non-domain / GraphQL) failure surfaces in the form-level banner.
 */
export function ActionFormDialog(props: ActionFormDialogProps): React.ReactElement | null {
  return props.open ? <ActionFormDialogOpening {...props} /> : null;
}

function ActionFormDialogOpening(props: ActionFormDialogProps): React.ReactElement {
  const t = useUiT();
  // Freeze the schema and seeds for this opening; live record refreshes update
  // submit context without replacing the user's draft or its original schema.
  const [resolved] = React.useState(() => {
    try {
      return { args: typeof props.action.args === "function" ? props.action.args(props.context) : props.action.args ?? EMPTY_ARGS };
    } catch (error) {
      return { error };
    }
  });
  if (resolved.args === undefined) {
    const banner = <ErrorBanner description={errorMessage(resolved.error, t("error.generic"))} />;
    return props.inline ? banner : (
      <DialogForm open={props.open} onOpenChange={props.onOpenChange} title={props.action.label}>{banner}</DialogForm>
    );
  }
  return <ActionArgsDialog {...props} args={resolved.args} />;
}

function ActionArgsDialog({
  action,
  context,
  open,
  onOpenChange,
  onSucceeded,
  inline = false,
  args: declaredArgs,
}: ActionFormDialogProps & { args: ActionArgs }): React.ReactElement {
  const t = useUiT();
  const toolbarHost = useRecordChromeContextMaybe()?.toolbarHost;
  const definition = isActionFormDefinition(declaredArgs) ? declaredArgs : undefined;
  const args = isActionFormDefinition(declaredArgs) ? EMPTY_ARGS : declaredArgs;
  const argNames = React.useMemo(
    () => new Set(definition?.fieldNames ?? (definition && typeof definition.fields !== "function"
      ? definition.fields.map((field) => field.name) : args.map((arg) => arg.name))),
    [args, definition],
  );
  const actionForm = useActionForm<ArgValues, ActionOutcome>({
    defaultValues: definition?.defaultValues ?? argDefaultValues(args, context),
    resolver: definition?.resolver,
    submit: async (collected) => {
      const result = await action.submit?.(serializeActionArgValues(args, collected), context);
      return result && "status" in result ? result : actionOutcomeSubmitResult(result);
    },
    onSuccess: (_values, outcome) => {
      onSucceeded?.(outcome);
      if (!inline) onOpenChange(false);
    },
    fieldNames: argNames,
  });
  const {
    formError,
    submitting,
    saveConflict,
    clearFieldError: clearServerError,
  } = actionForm;

  const form = actionForm.form;
  const run = () => {
    if (action.submit && !saveConflict) void actionForm.run();
  };

  const submitButton = (
    <ActionSubmitButton
      control={form.control}
      args={args}
      submitting={submitting}
      disabled={saveConflict}
      danger={action.danger}
      label={action.label}
      onSubmit={inline ? run : undefined}
    />
  );
  const body = (
    <>
      {definition ? <ActionDescriptorFields definition={definition} control={form.control} readOnly={saveConflict} disabled={submitting} /> : null}
      {args.map((arg) => (
        <Controller
          key={arg.name}
          control={form.control}
          name={arg.name}
          render={({ field, fieldState }) => (
            <ActionArgRow
              arg={arg}
              value={field.value}
              messages={fieldState.error ? fieldErrorMessages([fieldState.error]) : []}
              readOnly={submitting || saveConflict}
              onChange={(next) => {
                clearServerError(arg.name);
                field.onChange(next);
              }}
            />
          )}
        />
      ))}
      <ErrorBanner description={formError} />
    </>
  );

  // An inline form sits in a saved record's page, and forms cannot nest: its
  // fields stay in the page flow while its submit joins the record toolbar.
  if (inline) return (
    <ActionFormProvider {...form}>
      <div className="grid gap-4">{body}</div>
      {toolbarHost ? createPortal(submitButton, toolbarHost) : null}
    </ActionFormProvider>
  );
  return (
    <ActionFormProvider {...form}>
    <DialogForm
      open={open}
      onOpenChange={(next) => { if (!submitting) onOpenChange(next); }}
      title={action.label}
      size={definition?.size}
      footer={<>
        <Button type="button" variant="ghost" size="sm" onClick={() => onOpenChange(false)} disabled={submitting}>
          {t("dialog.cancel")}
        </Button>
        {submitButton}
      </>}
      onSubmit={(event) => { event.preventDefault(); run(); }}
    >
      {body}
    </DialogForm>
    </ActionFormProvider>
  );
}

/**
 * The submit button owns the live-values subscription for the required gate, so
 * a keystroke re-renders this button — not the whole dialog (the arg controls
 * keep their `Controller` isolation).
 */
function ActionSubmitButton({
  control,
  args,
  submitting,
  disabled,
  danger,
  label,
  onSubmit,
}: {
  control: Control<ArgValues>;
  args: readonly ActionArg[];
  submitting: boolean;
  disabled?: boolean;
  danger?: boolean;
  label: React.ReactNode;
  /** Run the submit directly instead of submitting an enclosing form. */
  onSubmit?: () => void;
}): React.ReactElement {
  const values = useWatch({ control }) as ArgValues;
  const preview = useRuntimeViewAs();
  const ready = args.every((arg) => arg.optional || !emptyDialogValue(values[arg.name]));
  return (
    <Button
      type={onSubmit ? "button" : "submit"}
      onClick={onSubmit}
      variant={danger ? "danger" : "primary"}
      size="sm"
      disabled={!ready || submitting || disabled || Boolean(preview.viewAs || preview.pending)}
      loading={submitting}
    >
      {label}
    </Button>
  );
}

function isActionFormDefinition(args: ActionArgs): args is ActionFormDefinition {
  return !Array.isArray(args);
}

function ActionDescriptorFields({ definition, control, readOnly, disabled }: {
  definition: ActionFormDefinition;
  control: Control<ArgValues>;
  readOnly: boolean;
  disabled: boolean;
}): React.ReactElement {
  const values = useWatch({ control });
  const fields = typeof definition.fields === "function" ? definition.fields(values) : definition.fields;
  return <fieldset disabled={readOnly || disabled} className="grid gap-4">
    {definition.content}
    <DescriptorFieldList fields={fields} readOnly={readOnly} />
  </fieldset>;
}

function ActionArgRow({
  arg,
  value,
  messages,
  readOnly,
  onChange,
}: {
  arg: ActionArg;
  value: unknown;
  messages?: readonly string[];
  readOnly?: boolean;
  onChange: (value: unknown) => void;
}): React.ReactElement {
  const label = arg.label ?? titleCase(arg.name);
  return (
    <FieldRoot>
      <FieldLabel optional={arg.optional}>{label}</FieldLabel>
      <ActionArgControl
        arg={arg}
        value={value}
        readOnly={readOnly}
        onChange={onChange}
      />
      {arg.description ? (
        <FieldDescription>{arg.description}</FieldDescription>
      ) : null}
      {messages && messages.length > 0 ? (
        <p className="mt-1 text-xs leading-5 text-danger-text">
          {messages.join(", ")}
        </p>
      ) : null}
    </FieldRoot>
  );
}

/** Route one arg to its control by kind — a scalar widget, a single relation
 * picker, or a relation-list multi-select — composing the shared owners. */
function ActionArgControl({
  arg,
  value,
  readOnly,
  onChange,
}: {
  arg: ActionArg;
  value: unknown;
  readOnly?: boolean;
  onChange: (value: unknown) => void;
}): React.ReactElement {
  if (arg.argKind === "relation") {
    return (
      <ActionRelationControl
        arg={arg}
        value={value}
        readOnly={readOnly}
        onChange={onChange}
      />
    );
  }
  if (arg.argKind === "relationList") {
    return (
      <ActionRelationListControl
        arg={arg}
        value={value}
        readOnly={readOnly}
        onChange={onChange}
      />
    );
  }
  return (
    <FieldDescriptorControl
      field={arg}
      value={value}
      disabled={readOnly}
      onChange={onChange}
    />
  );
}

function ActionRelationControl({
  arg,
  value,
  readOnly,
  onChange,
}: {
  arg: Extract<ActionArg, { argKind: "relation" }>;
  value: unknown;
  readOnly?: boolean;
  onChange: (value: unknown) => void;
}): React.ReactElement {
  const model = useModelMetadata(arg.resource);
  const relation = React.useMemo(
    () => relationFieldInfoForResource(arg.resource, model),
    [arg.resource, model],
  );
  if (!relation) {
    // Metadata not yet loaded / resource exposes no list root: fall back to the
    // descriptor's own widget rather than render a picker with no options.
    return (
      <FieldDescriptorControl
        field={arg}
        value={value}
        disabled={readOnly}
        onChange={onChange}
      />
    );
  }
  return (
    <RelationFieldWidget
      relation={relation}
      filters={arg.filters}
      create={arg.create ?? null}
      value={relationValueId(value)}
      readOnly={readOnly}
      placeholder={arg.placeholder}
      aria-label={typeof arg.label === "string" ? arg.label : arg.name}
      onChange={onChange}
    />
  );
}

function ActionRelationListControl({
  arg,
  value,
  readOnly,
  onChange,
}: {
  arg: Extract<ActionArg, { argKind: "relationList" }>;
  value: unknown;
  readOnly?: boolean;
  onChange: (value: unknown) => void;
}): React.ReactElement {
  const model = useModelMetadata(arg.resource);
  const relation = React.useMemo(
    () => relationFieldInfoForResource(arg.resource, model),
    [arg.resource, model],
  );
  if (!relation) {
    return (
      <FieldDescriptorControl
        field={arg}
        value={value}
        disabled={readOnly}
        onChange={onChange}
      />
    );
  }
  return (
    <RelationMultiFieldWidget
      relation={relation}
      filters={arg.filters}
      create={arg.create}
      value={Array.isArray(value) ? value : []}
      readOnly={readOnly}
      aria-label={typeof arg.label === "string" ? arg.label : arg.name}
      onChange={onChange}
    />
  );
}

const EMPTY_ARGS: readonly ActionArg[] = [];

/** Normalize relation lists and scalar values for an action's custom submit. */
export function serializeActionArgValues(
  args: readonly ActionArg[],
  values: ArgValues,
): ArgValues {
  const serialized = { ...values };
  for (const arg of args) {
    if (arg.argKind === "relationList") {
      serialized[arg.name] = relationIdList(values[arg.name]);
    } else if (
      (arg.argKind === undefined || arg.argKind === "scalar") &&
      (arg.kind === "datetime" || arg.widget === "datetime")
    ) {
      serialized[arg.name] = mutationDialogValueCodecs.datetime(values[arg.name]);
    }
  }
  return serialized;
}

/** Seed args from context or declared defaults; React Hook Form owns subsequent edits. */
function argDefaultValues(
  args: readonly ActionArg[],
  context: ActionFormContext,
): ArgValues {
  const values: ArgValues = {};
  for (const arg of args) {
    if (arg.argKind === "relationList") {
      const prefill = arg.fromContext ?? defaultRelationListPrefill;
      values[arg.name] = [...prefill(context)];
    } else if (arg.argKind === "relation") {
      values[arg.name] = relationValueId(arg.fromContext?.(context) ?? arg.defaultValue) ?? "";
    } else {
      values[arg.name] = arg.fromContext?.(context) ?? arg.defaultValue ?? emptyValueForField(arg);
    }
  }
  return values;
}

/** The invoking selection, else the open record's id — a relation list's default. */
function defaultRelationListPrefill(
  context: ActionFormContext,
): readonly string[] {
  if (context.selectedIds.length > 0) return context.selectedIds;
  const id = rowPublicId(context.record);
  return id ? [id] : [];
}
