import * as React from "react";
import { useBlocker, type ShouldBlockFn } from "@tanstack/react-router";

import { useConfirm } from "../../feedback";
import { useUiT } from "../../i18n";

/** A route move the guard weighs: where the person is and where the move goes. */
export type UnsavedChangesMove = Pick<Parameters<ShouldBlockFn>[0], "current" | "next">;

/**
 * Whether `move` changes the pathname: a move within it (search state only) keeps the form,
 * as a retained record tab does. Pass it as the guard's `leaves`.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function pathnameChanges({ current, next }: UnsavedChangesMove): boolean {
  return current.pathname !== next.pathname;
}

/** Ask whether to discard unsaved content, with the shared question: Leave resolves true, Stay false. */
export function useUnsavedChangesQuestion(): () => Promise<boolean> {
  const confirm = useConfirm();
  const t = useUiT();
  return React.useCallback(() => confirm({
    title: t("form.unsavedLeaveTitle"),
    cancel: t("form.stay"),
    confirm: t("form.leave"),
    danger: true,
  }), [confirm, t]);
}

/** Owns both in-app and before-unload protection for a dirty editable form. A move
 * `leaves` calls leaving asks first; by default every move leaves.
 * @owner FormView
 */
export function useUnsavedChangesNavigationGuard({
  isDirty,
  isDirtyNow,
  readOnly,
  leaves,
}: {
  isDirty: boolean;
  isDirtyNow: () => boolean;
  readOnly: boolean;
  /** Whether a route move abandons the form; omit for every move. `pathnameChanges` keeps search moves. */
  leaves?: (move: UnsavedChangesMove) => boolean;
}): () => Promise<boolean> {
  const ask = useUnsavedChangesQuestion();
  const requestLeave = React.useCallback(async () => {
    // Read the live store, not the captured render value. A successful save resets
    // the form (isDirty → false) and navigates in the same tick, before the React
    // re-render flushes — a stale `true` here would wrongly block the post-save
    // redirect with a phantom "unsaved changes" prompt.
    if (readOnly || !isDirtyNow()) return true;
    return ask();
  }, [ask, isDirtyNow, readOnly]);

  const shouldBlockFn = React.useCallback<ShouldBlockFn>(async (move) =>
    (leaves?.(move) ?? true) && !(await requestLeave()), [leaves, requestLeave]);

  useBlocker({
    shouldBlockFn,
    enableBeforeUnload: isDirty && !readOnly,
    disabled: readOnly || !isDirty,
  });
  return requestLeave;
}
