import * as React from "react";
import { holdsPermission, rowPublicId, type Row } from "@angee/metadata";
import { useMutation } from "@tanstack/react-query";

import { Button } from "../../ui/button";
import { DropdownMenu } from "../../ui/dropdown-menu";
import { Glyph } from "../../chrome/Glyph";
import { errorMessage, useConfirm, usePrompt, useToast } from "../../feedback";
import { ActionFormDialog } from "./ActionFormDialog";
import { ActionMenu, ActionTrigger } from "../../toolbars/ActionMenu";
import { ActionMenuContext } from "../../ui/action-menu-context";
import type { ActionDescriptor, ActionResult } from "../page";
import { useRuntimeViewAs } from "../../runtime";
import { useRecordChromeContextMaybe } from "../resource/record-chrome-context";
import { useLatestRef } from "../../lib/use-latest-ref";
import { useUiT } from "../../i18n";

/** The same permission-bearing descriptor used by `<Action>`. */
export type RecordActionDescriptor = ActionDescriptor;

export interface RecordDeleteAction {
  canDelete: boolean;
  isPending: boolean;
  onDelete: () => void;
}

interface ActionMutationVariables {
  action: RecordActionDescriptor;
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
  applyPatch = unavailablePatch,
  reload = noop,
  deleteAction,
  contributedActions,
  blocked: blockedByForm = false,
}: {
  record: Row | null;
  actions: readonly RecordActionDescriptor[];
  applyPatch?: (patch: Record<string, unknown>) => Promise<Row | null>;
  reload?: () => void | Promise<Row | null>;
  deleteAction?: RecordDeleteAction;
  /** Addon-contributed verbs rendered inside this same Actions menu. */
  contributedActions?: React.ReactNode;
  /** A dirty or pending form must be saved before acting on its persisted record. */
  blocked?: boolean;
}): React.ReactElement | null {
  const preview = useRuntimeViewAs();
  const t = useUiT();
  const menu = React.useContext(ActionMenuContext);
  const chrome = useRecordChromeContextMaybe();
  const blocked = blockedByForm || menu?.blocked || chrome?.actionsBlocked || Boolean(preview.viewAs || preview.pending);
  const blockedRef = useLatestRef(blocked);
  const confirm = useConfirm();
  const prompt = usePrompt();
  const toast = useToast();
  // The open typed-args action form (F-a), or null. Set after any confirm passes;
  // the dialog owns collecting the args and firing the action's `submit`.
  const [formAction, setFormAction] = React.useState<{ action: RecordActionDescriptor; fromMenu: boolean } | null>(
    null,
  );
  const actionMutation = useMutation<
    ActionResult,
    unknown,
    ActionMutationVariables
  >({
    mutationFn: async ({ action, values }) => {
      if (blockedRef.current || action.disabled || (action.permission && !holdsPermission(record, action.permission))) return;
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
    async (action: RecordActionDescriptor): Promise<void> => {
      if (blockedRef.current || action.disabled || (action.permission && !holdsPermission(record, action.permission))) return;
      if (action.confirm) {
        const confirmation =
          typeof action.confirm === "function" && record !== null
            ? action.confirm(record)
            : typeof action.confirm === "function"
              ? null
              : action.confirm;
        if (confirmation === null) return;
        const confirmed = await confirm({
          title: confirmation.title,
          ...(confirmation.body !== undefined
            ? { body: confirmation.body }
            : {}),
          ...(confirmation.danger !== undefined
            ? { danger: confirmation.danger }
            : {}),
          confirm: action.label,
        });
        if (!confirmed || blockedRef.current) return;
      }
      // A typed-args action collects its args (and merges the record/selection
      // context) in the dialog, which fires `submit` — not the string-only prompt.
      if (action.args && action.submit) {
        setFormAction({ action, fromMenu: menu !== null || action.placement !== "toolbar" });
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
    [actionMutation, blockedRef, confirm, prompt, record, menu],
  );

  // An action with a `visibleWhen` predicate shows only when the open record
  // matches (e.g. "Disable" only while enabled); a record must be loaded first.
  const visibleActions = actions.filter(
    (action) =>
      (!action.permission || holdsPermission(record, action.permission))
      && (!action.visibleWhen || (record != null && action.visibleWhen(record))),
  );
  const toolbarActions = visibleActions.filter((action) => action.placement === "toolbar");
  const menuActions = visibleActions.filter((action) => action.placement !== "toolbar");
  const visibleDeleteAction = deleteAction?.canDelete ? deleteAction : undefined;
  const disabled = (action: ActionDescriptor) =>
    blocked || Boolean(action.disabled) || pendingId !== null ||
    (recordId === null && !action.run && !action.submit);
  if (
    visibleActions.length === 0 &&
    visibleDeleteAction === undefined &&
    contributedActions == null &&
    formAction === null
  ) return null;

  const formDialog = formAction ? (
    <ActionFormDialog
      key={formAction.action.id}
      action={formAction.action}
      context={{
        record,
        selectedIds: recordId !== null ? [recordId] : [],
        refresh: async () => await reload() ?? null,
      }}
      open
      onOpenChange={(open) => { if (!open) setFormAction(null); }}
      onSucceeded={reload}
    />
  ) : null;
  const deleteTrigger = visibleDeleteAction !== undefined ? (
    <ActionTrigger variant="danger" glyph="trash"
      disabled={blocked || visibleDeleteAction.isPending}
      loading={visibleDeleteAction.isPending}
      onClick={() => { if (!blockedRef.current) visibleDeleteAction.onDelete(); }}>
      {t("actions.delete")}
    </ActionTrigger>
  ) : null;
  const renderAction = (action: RecordActionDescriptor) => (
    <ActionTrigger key={action.id} glyph={action.icon}
      variant={action.danger ? "danger" : "secondary"}
      disabled={disabled(action)} loading={pendingId === action.id}
      onClick={() => void runAction(action)}>
      {action.label}
    </ActionTrigger>
  );

  return (
    <>
      {menu ? <>
        {deleteTrigger}
        {visibleActions.map(renderAction)}
        {contributedActions}
        {formDialog}
      </> : <>
        {toolbarActions.map((action, index) => (
          <Button key={action.id} type="button" size="sm"
            variant={action.danger ? "danger" : action.primary && toolbarActions.findIndex((entry) => entry.primary) === index ? "primary" : "secondary"}
            disabled={disabled(action)} loading={pendingId === action.id} onClick={() => void runAction(action)}>
            {action.icon ? <Glyph name={action.icon} /> : null}
            {action.label}
          </Button>
        ))}
        {menuActions.length > 0 || visibleDeleteAction !== undefined || contributedActions != null || formAction?.fromMenu ? <ActionMenu blocked={blocked} loading={pendingId !== null}>
          {deleteTrigger}
          {visibleDeleteAction !== undefined && menuActions.length > 0 ? (
            <DropdownMenu.Separator />
          ) : null}
          {menuActions.map(renderAction)}
          {contributedActions}
          {formAction?.fromMenu ? formDialog : null}
        </ActionMenu> : null}
        {formAction && !formAction.fromMenu ? formDialog : null}
      </>}
    </>
  );
}

function noop(): void {}

async function unavailablePatch(): Promise<never> {
  throw new Error("A patch action requires its record form's applyPatch binding.");
}

// A rich (non-string) label can't title a toast; fall back to the action id so
// the toast still names the action that failed.
function actionLabelText(action: ActionDescriptor): string {
  return typeof action.label === "string" ? action.label : action.id;
}
