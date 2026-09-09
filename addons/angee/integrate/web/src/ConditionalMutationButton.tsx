import type { ActionFieldName } from "@angee/gql/console/actions";
import { type Row } from "@angee/metadata";
import {
  ActionFormDialog,
  Button,
  Glyph,
  useConfirm,
  useRecordChromeActionMutation,
  useRecordChromeActionOutcome,
  useRecordChromeContext,
  type ActionArg,
  type ActionConfirm,
  type ButtonVariant,
  type RecordChromeContext,
} from "@angee/ui";
import * as React from "react";

/**
 * The record-chrome context a predicate runs against, narrowed to the loaded
 * record the button gates on. Derived from the owning context so a new chrome
 * fact reaches predicates without a second declaration drifting from it.
 */
export type ConditionalMutationButtonContext = Omit<
  RecordChromeContext,
  "record"
> & { record: Row };

interface ConditionalMutationButtonBase<TField extends ActionFieldName> {
  field: TField;
  label: React.ReactNode;
  when: (context: ConditionalMutationButtonContext) => boolean;
  glyph?: string;
  /**
   * Typed arguments the verb collects before it fires (re-enter a password,
   * pick a target). Declared, the click opens the shared `ActionFormDialog`,
   * which sends the collected values beside the record id and binds the
   * outcome's in-band `validationErrors` to its inputs; omitted, the verb
   * fires on the id alone.
   */
  args?: readonly ActionArg[];
}

/**
 * A destructive verb declares its confirm with its variant: `variant="danger"`
 * *requires* `confirm`, so a vendor specializing an inherited destructive verb
 * cannot quietly ship the cheaper click. (`disconnect_whatsapp_channel` did
 * exactly that — it replaced a confirmed Disconnect while doing strictly more.)
 */
export type ConditionalMutationButtonProps<
  TField extends ActionFieldName = ActionFieldName,
> = ConditionalMutationButtonBase<TField> &
  (
    | { variant: "danger"; confirm: ActionConfirm }
    | { variant?: Exclude<ButtonVariant, "danger">; confirm?: ActionConfirm }
  );

/**
 * A record-chrome button that conditionally runs one generated single-id
 * ActionResult mutation, optionally behind a confirm and/or a typed-args form.
 *
 * Domain addons own the predicate, the mutation choice, the confirm copy, and
 * the arg declaration; `useRecordChromeActionMutation` owns the dispatch,
 * feedback, and invalidation of an id-only verb, and `ActionFormDialog` (over
 * `useRecordChromeActionOutcome`) owns collecting args and settling their outcome.
 * This is the *contributed* counterpart of a form's declared `<Action>`, which
 * `RecordActionBar` renders: `FormView` parses section-slot content statically,
 * so a contributed `<Action>`'s `run` could never be bound to a mutation hook.
 * Fold this into `RecordActionBar` once an action registry can dispatch a
 * declared action by name — the seam `integrate/schema.py` already anticipates —
 * and delete it.
 */
export function ConditionalMutationButton<
  TField extends ActionFieldName = ActionFieldName,
>({
  args,
  confirm,
  field,
  glyph,
  label,
  variant = "secondary",
  when,
}: ConditionalMutationButtonProps<TField>): React.ReactElement | null {
  const askConfirm = useConfirm();
  const {
    canonicalResource,
    dataProviderName,
    resource,
    recordId,
    record,
  } = useRecordChromeContext();
  const [mutate, mutation] = useRecordChromeActionMutation<TField>(field);
  const [mutateOutcome] = useRecordChromeActionOutcome<TField>(field);
  const [formOpen, setFormOpen] = React.useState(false);
  // Derived during render: the only consumer is this component's own `onClick`,
  // which is a fresh arrow each render regardless, so a memo would buy nothing —
  // and `confirm` arrives as an object literal, so it could never hit anyway.
  const run = async (): Promise<void> => {
    if (confirm) {
      const confirmed = await askConfirm({
        title: confirm.title,
        ...(confirm.body !== undefined ? { body: confirm.body } : {}),
        ...(confirm.danger !== undefined ? { danger: confirm.danger } : {}),
        confirm: label,
      });
      if (!confirmed) return;
    }
    if (args) {
      setFormOpen(true);
      return;
    }
    await mutate(recordId);
  };

  if (
    record === null ||
    !when({ canonicalResource, dataProviderName, resource, recordId, record })
  ) {
    return null;
  }

  return (
    <>
      <Button
        type="button"
        variant={variant}
        size="sm"
        loading={mutation.fetching}
        onClick={() => {
          void run();
        }}
      >
        {glyph ? <Glyph decorative name={glyph} /> : null}
        {label}
      </Button>
      {args ? (
        <ActionFormDialog
          action={{
            id: field,
            label,
            args,
            submit: (values) => mutateOutcome(recordId, values),
          }}
          context={{ record, selectedIds: [recordId] }}
          open={formOpen}
          onOpenChange={setFormOpen}
        />
      ) : null}
    </>
  );
}
