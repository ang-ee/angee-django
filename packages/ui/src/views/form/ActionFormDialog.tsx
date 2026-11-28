import * as React from "react";
import { createPortal } from "react-dom";
import { useWatch, type Control } from "react-hook-form";
import type { ActionOutcome } from "@angee/refine";

import { DialogForm } from "../../fragments/DialogForm";
import { ErrorBanner } from "../../fragments/ErrorBanner";
import { errorMessage } from "../../feedback";
import { Button } from "../../ui/button";
import { useUiT } from "../../i18n";
import { titleCase } from "../../lib/titleCase";
import { useIdempotencyDraftOwner, useIdempotencyKeys } from "../../lib/idempotency-key";
import { relationIdList, relationValueId } from "../../widgets/types";
import { useRuntimeViewAs } from "../../runtime";
import { emptyValueForField } from "./field-values";
import {
  mutationDialogValueCodecs,
  dialogRuleErrors,
} from "./MutationDialog";
import { useRecordChromeContextMaybe } from "../resource/record-chrome-context";
import { useActionForm } from "./use-action-form";
import { ActionFormProvider } from "./ActionFormProvider";
import { DescriptorFieldList, type DescriptorField } from "./DescriptorFieldList";
import { fieldValidationSummary, shownFields, visibleFields } from "./form-view-model";
import { actionOutcomeSubmitResult, formSubmitOutcome, markMemberErrors } from "./validation-errors";
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
 * `DescriptorFieldList`) with react-hook-form for collection, and `useActionForm` for the submit
 * lifecycle; no hand-rolled `<form>`.
 *
 * On an `ok=false` outcome it binds each in-band reason under the field it names
 * that the dialog renders, shows the rest (a non-field reason, one under a hidden
 * field or an inactive branch) in its banner, and stays open; a reason under a name
 * only a `content` editor holds shows in both places, under that editor (when it
 * reads its field's error) and on the banner. It closes only on `ok=true`, toasting
 * the success `message`.
 * A thrown (non-domain / GraphQL) failure surfaces in the form-level banner. While
 * the submit runs it cannot be dismissed, and dismissing typed values asks first
 * (`DialogForm`'s `pending` and `dirty`), so it renders under the app's router and
 * `ModalsHost`: opened without the router it throws
 * `useUnsavedChangesNavigationGuard must be used under the app's router.` A test provides both
 * through the shared providers (`createUiTestProviders` or `AppShellTestProviders` with
 * `router`).
 * @kind surface
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
  const fields = React.useMemo(() => definition?.fields ?? args.map(actionArgField), [args, definition]);
  // Without a resolver the form checks its own rules at each change: the changed field shows the
  // messages of the rules it breaks under it (an object field under each failing member, edited
  // or not), while a value that breaks a rule from the opening disables the submit silently until
  // its field is edited. A resolver reports constraints at submit.
  const validateConstraints = !definition?.resolver;
  // Set when an accepted submit closes the dialog: `onSucceeded` may move on in the same tick,
  // before the close renders, and the dialog then holds nothing a move would lose.
  const accepted = React.useRef(false);
  // An action declaring the draft it writes holds one idempotency key for it, from its first attempt.
  const keys = useIdempotencyKeys();
  const draftScope = action.idempotencyScope?.(context) ?? null;
  useIdempotencyDraftOwner(open || inline ? draftScope : null, `the form of action "${action.id}"`);
  const actionForm = useActionForm<ArgValues, ActionOutcome>({
    ...(draftScope ? { answerLost: t("form.answerLostResend") } : {}),
    defaultValues: definition?.defaultValues ?? argDefaultValues(args, context),
    mode: validateConstraints ? "onChange" : "onSubmit",
    resolver: async (values, resolverContext, options) => {
      const declared = typeof fields === "function" ? fields(values) : fields;
      const errors = markMemberErrors(dialogRuleErrors(declared, values, t, validateConstraints), visibleFields(declared, values));
      if (Object.keys(errors).length) return { values: {}, errors };
      return definition?.resolver
        ? definition.resolver(values, resolverContext, options)
        : { values, errors: {} };
    },
    submit: async (collected, options) => {
      const values = serializeActionArgValues(args, collected);
      const submitted = async (idempotencyKey?: string) => {
        const result = await action.submit?.(values, idempotencyKey ? { ...context, idempotencyKey } : context, options);
        return result && "status" in result ? result : actionOutcomeSubmitResult(result);
      };
      return draftScope ? keys.send(draftScope, submitted, formSubmitOutcome) : submitted();
    },
    onSuccess: (_values, outcome) => {
      if (!inline) accepted.current = true;
      onSucceeded?.(outcome);
      if (!inline) onOpenChange(false);
    },
    // The fields the dialog renders when the answer lands, in its order: a reason under any other
    // name (a hidden field, a field of an inactive schema branch) shows on the banner.
    shownFieldNames: (values) => shownFields(typeof fields === "function" ? fields(values) : fields, values).map((field) => field.name),
    // The banner's line naming the refused fields, by the labels the dialog shows.
    fieldSummary: (fieldErrors: Record<string, readonly string[]>): string => fieldValidationSummary(fieldErrors, new Map(
      (typeof fields === "function" ? fields(actionForm.form.getValues()) : fields).map((field) => [field.name, field]),
    ), t),
  });
  const {
    formErrors,
    banner,
    submitting,
    saveConflict,
  } = actionForm;

  const form = actionForm.form;
  const run = () => {
    if (action.submit && !saveConflict) void actionForm.run();
  };

  const submitButton = (
    <ActionSubmitButton
      control={form.control}
      fields={fields}
      validateConstraints={validateConstraints}
      submitting={submitting}
      disabled={saveConflict}
      danger={action.danger}
      label={action.submitLabel ?? action.label}
      onSubmit={inline ? run : undefined}
    />
  );
  const body = (
    <>
      {definition ? <ActionDescriptorFields definition={definition} control={form.control} readOnly={saveConflict} disabled={submitting} /> : null}
      {!definition ? <DescriptorFieldList fields={args.map(actionArgField)} readOnly={saveConflict} unregisterHidden /> : null}
      <ErrorBanner {...banner} messages={formErrors} />
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
      onOpenChange={(next) => {
        // A dismissal gives the draft up; the accepted close has ended it already.
        if (!next && draftScope) keys.discard(draftScope);
        onOpenChange(next);
      }}
      pending={submitting}
      dirty={form.formState.isDirty}
      holdsWork={() => !accepted.current && (form.formState.isDirty || form.formState.isSubmitting)}
      title={action.label}
      size={definition?.size}
      footer={({ dismiss, dismissRefusal }) => <>
        <Button type="button" variant="ghost" size="sm" onClick={dismiss} disabledReason={dismissRefusal}>
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
  fields,
  validateConstraints,
  submitting,
  disabled,
  danger,
  label,
  onSubmit,
}: {
  control: Control<ArgValues>;
  fields: ActionFormDefinition["fields"];
  validateConstraints: boolean;
  submitting: boolean;
  disabled?: boolean;
  danger?: boolean;
  label: React.ReactNode;
  /** Run the submit directly instead of submitting an enclosing form. */
  onSubmit?: () => void;
}): React.ReactElement {
  const values = useWatch({ control }) as ArgValues;
  const preview = useRuntimeViewAs();
  const t = useUiT();
  const ready = Object.keys(dialogRuleErrors(fields, values, t, validateConstraints)).length === 0;
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
    <DescriptorFieldList fields={fields} readOnly={readOnly} unregisterHidden />
  </fieldset>;
}

/** Translate action declarations into the canonical descriptor form contract. */
function actionArgField(arg: ActionArg): DescriptorField {
  const field: DescriptorField = {
    ...arg,
    label: arg.label ?? titleCase(arg.name),
    required: !arg.optional,
  };
  if (arg.argKind === "relation" || arg.argKind === "relationList") {
    field.widget = arg.argKind === "relationList" ? "many2many" : "many2one";
    field.relation = {
      resource: arg.resource,
      filters: arg.filters,
      create: arg.create,
      ...(arg.argKind === "relation" ? {
        optionDescriptionField: arg.optionDescriptionField,
        optionStatusField: arg.optionStatusField,
        optionStatusArguments: arg.optionStatusArguments,
      } : {}),
    };
  }
  return field;
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

/**
 * Seed args from context or declared defaults, else a scalar arg's empty value (`emptyValueForField`:
 * `null` for a date, time or number widget); React Hook Form owns subsequent edits.
 */
function argDefaultValues(
  args: readonly ActionArg[],
  context: ActionFormContext,
): ArgValues {
  const values: ArgValues = {};
  for (const arg of args) {
    if (arg.argKind === "relationList") {
      // Empty unless the action says which records seed it: the invoking ids are
      // of the invoking model, which a list of another model must never inherit.
      values[arg.name] = [...(arg.fromContext?.(context) ?? [])];
    } else if (arg.argKind === "relation") {
      values[arg.name] = relationValueId(arg.fromContext?.(context) ?? arg.defaultValue) ?? "";
    } else {
      values[arg.name] = arg.fromContext?.(context) ?? arg.defaultValue ?? emptyValueForField(arg);
    }
  }
  return values;
}
