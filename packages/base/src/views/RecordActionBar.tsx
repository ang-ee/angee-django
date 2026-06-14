import * as React from "react";
import type { Row } from "@angee/sdk";

import { Button } from "../ui/button";
import { DropdownMenu } from "../ui/dropdown-menu";
import { Glyph } from "../chrome/Glyph";
import { useConfirm, usePrompt, useToast } from "../feedback";
import type { ActionDescriptor } from "./page";

export interface RecordDeleteAction {
  canDelete: boolean;
  isPending: boolean;
  onDelete: () => void;
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
  const [pendingId, setPendingId] = React.useState<string | null>(null);
  // A `run` action often closes/navigates the record, unmounting this bar before
  // its mutation resolves; guard the trailing setState against that.
  const mountedRef = React.useRef(true);
  React.useEffect(() => () => {
    mountedRef.current = false;
  }, []);

  const recordId = typeof record?.id === "string" ? record.id : null;

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
      let values: Record<string, string> = {};
      if (action.prompt) {
        const result = await prompt(action.prompt);
        if (result === null) return;
        values = result;
      }

      setPendingId(action.id);
      try {
        if (action.run) {
          const message = await action.run({
            record,
            values,
            refresh: reload,
            update: applyPatch,
            prompt,
          });
          if (typeof message === "string" && message) {
            toast.success({ title: message });
          }
        } else {
          await applyPatch({ ...(action.set ?? {}), ...values });
        }
      } catch (error) {
        toast.error({
          title: actionLabelText(action),
          description:
            error instanceof Error ? error.message : "The action failed.",
        });
      } finally {
        if (mountedRef.current) setPendingId(null);
      }
    },
    [applyPatch, confirm, prompt, record, reload, toast],
  );

  // An action with a `visibleWhen` predicate shows only when the open record
  // matches (e.g. "Disable" only while enabled); a record must be loaded first.
  const visibleActions = actions.filter(
    (action) =>
      !action.visibleWhen || (record != null && action.visibleWhen(record)),
  );
  if (visibleActions.length === 0 && deleteAction === undefined) return null;

  return (
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
                  (recordId === null && !action.run)
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
  );
}

// A rich (non-string) label can't title a toast; fall back to the action id so
// the toast still names the action that failed.
function actionLabelText(action: ActionDescriptor): string {
  return typeof action.label === "string" ? action.label : action.id;
}
