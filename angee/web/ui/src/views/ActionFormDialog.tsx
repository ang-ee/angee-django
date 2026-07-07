import * as React from "react";
import { Controller, useForm, useWatch, type Control } from "react-hook-form";
import { rowPublicId, useModelMetadata } from "@angee/metadata";

import { DialogForm } from "../fragments/DialogForm";
import { ErrorBanner } from "../fragments/ErrorBanner";
import { Button } from "../ui/button";
import { FieldDescription, FieldLabel, FieldRoot } from "../ui/field";
import { useUiT } from "../i18n";
import { titleCase } from "../lib/titleCase";
import { relationValueId } from "../widgets/types";
import { FieldDescriptorControl } from "./field-descriptor-control";
import { emptyDialogValue, emptyValueForField } from "./MutationDialog";
import { relationFieldInfoForResource } from "./model-metadata-defaults";
import { RelationFieldWidget } from "./RelationFieldWidget";
import { RelationMultiFieldWidget } from "./RelationMultiFieldWidget";
import { useActionForm } from "./use-action-form";
import type { ActionArg, ActionDescriptor, ActionFormContext } from "./page";

export interface ActionFormDialogProps {
  /** The action being collected — must declare `args` and `submit`. */
  action: ActionDescriptor;
  /** The invoking record/selection a `relationList` arg prefills from. */
  context: ActionFormContext;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called once after a successful (`ok`) submit — e.g. to reload the record. */
  onSucceeded?: () => void;
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
export function ActionFormDialog({
  action,
  context,
  open,
  onOpenChange,
  onSucceeded,
}: ActionFormDialogProps): React.ReactElement {
  const t = useUiT();
  const args = action.args ?? EMPTY_ARGS;
  const argNames = React.useMemo(
    () => new Set(args.map((arg) => arg.name)),
    [args],
  );
  const form = useForm<ArgValues>({
    // Seeded once at mount — RHF reads defaultValues only then. The caller mounts
    // the dialog per invocation (RecordActionBar keys it by action id and unmounts
    // on close), which is what re-seeds the relationList prefill for each open.
    defaultValues: argDefaultValues(args, context),
  });
  // `useActionForm` owns the fire→outcome→bind-errors→toast/close lifecycle; this
  // dialog owns only the value collection (RHF args) and rendering.
  const actionForm = useActionForm<ArgValues>({
    submit: (collected) => {
      // `run` is reached only when `action.submit` is set (guarded below); the
      // fallback just keeps the return total for the optional descriptor field.
      if (!action.submit) return { ok: true, message: "" };
      return action.submit(collected, context);
    },
    onSuccess: () => {
      onSucceeded?.();
      onOpenChange(false);
    },
    fieldNames: argNames,
  });
  const {
    fieldErrors: serverErrors,
    formError,
    submitting,
    clearFieldError: clearServerError,
  } = actionForm;

  const submit = form.handleSubmit(async (collected) => {
    if (!action.submit) return;
    await actionForm.run(collected);
  });

  const footer = (
    <>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => onOpenChange(false)}
      >
        {t("dialog.cancel")}
      </Button>
      <ActionSubmitButton
        control={form.control}
        args={args}
        submitting={submitting}
        label={action.label}
      />
    </>
  );

  return (
    <DialogForm
      open={open}
      onOpenChange={onOpenChange}
      title={action.label}
      footer={footer}
      onSubmit={(event) => void submit(event)}
    >
      {args.map((arg) => (
        <Controller
          key={arg.name}
          control={form.control}
          name={arg.name}
          render={({ field }) => (
            <ActionArgRow
              arg={arg}
              value={field.value}
              messages={serverErrors[arg.name]}
              readOnly={submitting}
              onChange={(next) => {
                clearServerError(arg.name);
                field.onChange(next);
              }}
            />
          )}
        />
      ))}
      <ErrorBanner description={formError} />
    </DialogForm>
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
  label,
}: {
  control: Control<ArgValues>;
  args: readonly ActionArg[];
  submitting: boolean;
  label: React.ReactNode;
}): React.ReactElement {
  const values = useWatch({ control }) as ArgValues;
  const ready = args.every(
    (arg) => arg.optional || !emptyDialogValue(values[arg.name]),
  );
  return (
    <Button
      type="submit"
      variant="primary"
      size="sm"
      disabled={!ready || submitting}
    >
      {label}
    </Button>
  );
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
      readOnly={readOnly}
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
        readOnly={readOnly}
        onChange={onChange}
      />
    );
  }
  return (
    <RelationFieldWidget
      relation={relation}
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
        readOnly={readOnly}
        onChange={onChange}
      />
    );
  }
  return (
    <RelationMultiFieldWidget
      relation={relation}
      value={Array.isArray(value) ? value : []}
      readOnly={readOnly}
      aria-label={typeof arg.label === "string" ? arg.label : arg.name}
      onChange={onChange}
    />
  );
}

const EMPTY_ARGS: readonly ActionArg[] = [];

/** Seed each arg: a relation list prefills from context; scalars/relations empty. */
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
      values[arg.name] = "";
    } else {
      values[arg.name] = emptyValueForField(arg);
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
