import * as React from "react";
import type { Row } from "@angee/sdk";

import { Button } from "../ui/button";
import { Glyph } from "../chrome/Glyph";
import { useConfirm, usePrompt, useToast } from "../feedback";
import type { ActionDescriptor } from "./page";

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
}: {
  record: Row | null;
  actions: readonly ActionDescriptor[];
  applyPatch: (patch: Record<string, unknown>) => Promise<Row | null>;
  reload: () => void;
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
        toast.danger({
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
  if (visibleActions.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      {visibleActions.map((action) => (
        <Button
          key={action.id}
          type="button"
          size="sm"
          variant={action.danger ? "danger" : "secondary"}
          // A declarative (`set`/`prompt`) action needs an open record id; a
          // custom `run` may not, so leave it enabled.
          disabled={
            Boolean(action.disabled) || (recordId === null && !action.run)
          }
          loading={pendingId === action.id}
          onClick={() => void runAction(action)}
        >
          {action.icon ? <Glyph name={action.icon} /> : null}
          {action.label}
        </Button>
      ))}
    </div>
  );
}

// A rich (non-string) label can't title a toast; fall back to the action id so
// the toast still names the action that failed.
function actionLabelText(action: ActionDescriptor): string {
  return typeof action.label === "string" ? action.label : action.id;
}
