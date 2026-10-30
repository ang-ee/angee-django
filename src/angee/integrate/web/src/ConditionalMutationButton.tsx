import type { ActionFieldName } from "@angee/gql/console/actions";
import { type Row } from "@angee/metadata";
import {
  RecordVerb,
  useRecordChromeActionMutation,
  useRecordChromeActionOutcome,
  useRecordChromeContext,
  type ActionArg,
  type ActionConfirm,
  type ActionDescriptor,
  type ButtonVariant,
  type RecordChromeContext,
} from "@angee/ui";
import * as React from "react";

/**
 * The record-chrome context a predicate runs against, narrowed to the loaded
 * record the verb gates on. Derived from the owning context so a new chrome
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
   * pick a target). Declared, the verb opens the shared action form, which sends
   * the collected values beside the record id and binds the outcome's in-band
   * `validationErrors` to its inputs; omitted, the verb fires on the id alone.
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
 * A contributed record verb that conditionally runs one generated single-id
 * ActionResult mutation. The addon owns the predicate, the mutation choice, the
 * confirm copy and the arg declaration; the descriptor it builds goes through
 * `RecordVerb` into `RecordActionBar`, which owns the confirm, the args form,
 * the pending state and the outcome feedback like every other record verb.
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
  const {
    canonicalResource,
    dataProviderName,
    resource,
    recordId,
    record,
    formReadOnly,
  } = useRecordChromeContext();
  const [mutate] = useRecordChromeActionMutation<TField>(field);
  const [mutateOutcome] = useRecordChromeActionOutcome<TField>(field);

  if (
    record === null ||
    !when({ canonicalResource, dataProviderName, resource, recordId, record, formReadOnly })
  ) {
    return null;
  }

  const action: ActionDescriptor = {
    id: field,
    label,
    icon: glyph,
    placement: "toolbar",
    danger: variant === "danger",
    primary: variant === "primary",
    ...(confirm ? { confirm } : {}),
    ...(args
      ? { args, submit: (values) => mutateOutcome(recordId, values) }
      : { run: async () => { await mutate(recordId); } }),
  };
  return <RecordVerb action={action} />;
}
