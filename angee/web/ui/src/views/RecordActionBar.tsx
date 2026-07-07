import * as React from "react";
import { rowPublicId, type Row } from "@angee/metadata";
import { useMutation } from "@tanstack/react-query";

import { Button } from "../ui/button";
import { DropdownMenu } from "../ui/dropdown-menu";
import { Glyph } from "../chrome/Glyph";
import { errorMessage, useConfirm, usePrompt, useToast } from "../feedback";
import { ActionFormDialog } from "./ActionFormDialog";
import type { ActionDescriptor, ActionResult } from "./page";

export interface RecordDeleteAction {
  canDelete: boolean;
  isPending: boolean;
  onDelete: () => void;
}

interface ActionMutationVariables {
  action: ActionDescriptor;
  values: Record<string, string>;
}

/**
 * Render a record's domain actions and run them against the open record.
 *
 * Each action either applies a declarative `set` patch (toggles, revoke, reset)
 * or calls an imperative `run` for a custom mutation. A `confirm` gates it; a
 * `prompt` collects input first — those values merge into the `set` patch or
 * reach `run` via its context. The patch/refresh come from the form (which owns
 * the field selection and re-seeds itself); errors surface as a toast and a
 * `run` may return a success message.
 */
export function RecordActionBar({
  record,
  actions,
  applyPatch,
  reload,
  deleteAction,
}: {
  record: Row | null;
  actions: readonly ActionDescriptor[];
  applyPatch: (patch: Record<string, unknown>) => Promise<Row | null>;
  reload: () => void;
  deleteAction?: RecordDeleteAction;
}): React.ReactElement | null {
  const confirm = useConfirm();
  const prompt = usePrompt();
  const toast = useToast();
  // The open typed-args action form (F-a), or null. Set after any confirm passes;
  // the dialog owns collecting the args and firing the action's `submit`.
  const [formAction, setFormAction] = React.useState<ActionDescriptor | null>(
    null,
  );
  const actionMutation = useMutation<
    ActionResult,
    unknown,
    ActionMutationVariables
  >({
    mutationFn: async ({ action, values }) => {
      if (action.run) {
        return action.run({
          record,
          values,
          refresh: reload,
          update: applyPatch,
          prompt,
        });
      }
      await applyPatch({ ...(action.set ?? {}), ...values });
      return undefined;
    },
    onSuccess: (message) => {
      if (typeof message === "string" && message) {
        toast.success({ title: message });
      }
    },
    onError: (error, { action }) => {
      toast.danger({
        title: actionLabelText(action),
        description: errorMessage(error, "The action failed."),
      });
    },
  });
  const pendingId = actionMutation.isPending
    ? (actionMutation.variables?.action.id ?? null)
    : null;

  const recordId = rowPublicId(record);

  const runAction = React.useCallback(
    async (action: ActionDescriptor): Promise<void> => {
      if (action.confirm) {
        const confirmed = await confirm({
          title: action.confirm.title,
          ...(action.confirm.body !== undefined
            ? { body: action.confirm.body }
            : {}),
          ...(action.confirm.danger !== undefined
            ? { danger: action.confirm.danger }
            : {}),
          confirm: action.label,
        });
        if (!confirmed) return;
      }
      // A typed-args action collects its args (and merges the record/selection
      // context) in the dialog, which fires `submit` — not the string-only prompt.
      if (action.args && action.submit) {
        setFormAction(action);
        return;
      }
      let values: Record<string, string> = {};
      if (action.prompt) {
        const result = await prompt(action.prompt);
        if (result === null) return;
        values = result;
      }

      await actionMutation
        .mutateAsync({ action, values })
        .catch(() => undefined);
    },
    [actionMutation, confirm, prompt],
  );

  // An action with a `visibleWhen` predicate shows only when the open record
  // matches (e.g. "Disable" only while enabled); a record must be loaded first.
  const visibleActions = actions.filter(
    (action) =>
      !action.visibleWhen || (record != null && action.visibleWhen(record)),
  );
  if (visibleActions.length === 0 && deleteAction === undefined) return null;

  return (
    <>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger
          render={
            // A DropdownMenu.Item closes the menu on click, so the item's
            // pendingId-disabled state is never seen. Drive the affordance from the
            // trigger instead: while any action runs it shows loading and is
            // disabled, so a slow non-navigating action gives feedback and can't be
            // re-fired from a reopened menu.
            <Button
              type="button"
              variant="ghost"
              size="md"
              loading={pendingId !== null}
            >
              <Glyph name="more-vertical" />
              Actions
            </Button>
          }
        />
        <DropdownMenu.Portal>
          <DropdownMenu.Positioner sideOffset={6} align="start">
            <DropdownMenu.Content className="w-52">
              {deleteAction !== undefined ? (
                <DropdownMenu.Item
                  variant="danger"
                  disabled={!deleteAction.canDelete || deleteAction.isPending}
                  onClick={deleteAction.onDelete}
                >
                  <Glyph name="trash" />
                  Delete
                </DropdownMenu.Item>
              ) : null}
              {deleteAction !== undefined && visibleActions.length > 0 ? (
                <DropdownMenu.Separator />
              ) : null}
              {visibleActions.map((action) => (
                <DropdownMenu.Item
                  key={action.id}
                  variant={action.danger ? "danger" : "default"}
                  disabled={
                    Boolean(action.disabled) ||
                    pendingId === action.id ||
                    (recordId === null && !action.run && !action.submit)
                  }
                  onClick={() => void runAction(action)}
                >
                  {action.icon ? <Glyph name={action.icon} /> : null}
                  {action.label}
                </DropdownMenu.Item>
              ))}
            </DropdownMenu.Content>
          </DropdownMenu.Positioner>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
      {formAction ? (
        <ActionFormDialog
          key={formAction.id}
          action={formAction}
          context={{
            record,
            selectedIds: recordId !== null ? [recordId] : [],
          }}
          open
          onOpenChange={(open) => {
            if (!open) setFormAction(null);
          }}
          onSucceeded={reload}
        />
      ) : null}
    </>
  );
}

// A rich (non-string) label can't title a toast; fall back to the action id so
// the toast still names the action that failed.
function actionLabelText(action: ActionDescriptor): string {
  return typeof action.label === "string" ? action.label : action.id;
}
