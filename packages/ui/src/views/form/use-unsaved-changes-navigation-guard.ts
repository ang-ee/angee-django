import * as React from "react";
import { useBlocker, useRouter, type ShouldBlockFn } from "@tanstack/react-router";

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

/** Ask whether to leave what a form holds, with the shared question: Leave resolves true, Stay
 * false. Asked while a submission runs, it says that the submission goes on, not that changes
 * are unsaved. */
export function useUnsavedChangesQuestion(): (whilePending?: boolean) => Promise<boolean> {
  const confirm = useConfirm();
  const t = useUiT();
  return React.useCallback((whilePending = false) => confirm({
    title: t(whilePending ? "form.pendingLeaveTitle" : "form.unsavedLeaveTitle"),
    ...(whilePending ? { body: t("form.pendingLeaveBody") } : {}),
    cancel: t("form.stay"),
    confirm: t("form.leave"),
    danger: true,
  }), [confirm, t]);
}

/** Owns both in-app and before-unload protection for a dirty editable form. A move
 * `leaves` calls leaving asks first; by default every move leaves. It renders under the app's
 * router and `ModalsHost`, whatever the form holds, and throws at render when one is missing:
 * `useUnsavedChangesNavigationGuard must be used under the app's router.`, or
 * `useConfirm must be used under ModalsHost.`. Its callers render under them so: a `DialogForm`
 * given `dirty` (so `MutationDialog` and the typed-args action dialog), `FormView`, and a page
 * guarding its own edits. A test or a story provides both through the shared providers:
 * `createUiTestProviders` or `AppShellTestProviders` with `router`, `@angee/app/testing`'s
 * `ShellPageTestProviders`, or the workshop's routed fixtures.
 * @owner FormView
 */
export function useUnsavedChangesNavigationGuard({
  isDirty,
  isDirtyNow,
  readOnly,
  leaves,
  pendingNow,
}: {
  isDirty: boolean;
  /** Whether the form holds what a move would lose now, read live when a move is asked. */
  isDirtyNow: () => boolean;
  readOnly: boolean;
  /** Whether a route move abandons the form; omit for every move. `pathnameChanges` keeps search moves. */
  leaves?: (move: UnsavedChangesMove) => boolean;
  /** Whether a submission runs now: the question then says it goes on, not that changes are unsaved. */
  pendingNow?: () => boolean;
}): () => Promise<boolean> {
  const ask = useUnsavedChangesQuestion();
  if (!useRouter({ warn: false })) {
    throw new Error("useUnsavedChangesNavigationGuard must be used under the app's router.");
  }
  const requestLeave = React.useCallback(async () => {
    // Read the live store, not the captured render value. A successful save resets
    // the form (isDirty → false) and navigates in the same tick, before the React
    // re-render flushes — a stale `true` here would wrongly block the post-save
    // redirect with a phantom "unsaved changes" prompt.
    if (readOnly || !isDirtyNow()) return true;
    return ask(pendingNow?.() ?? false);
  }, [ask, isDirtyNow, pendingNow, readOnly]);

  const shouldBlockFn = React.useCallback<ShouldBlockFn>(async (move) =>
    (leaves?.(move) ?? true) && !(await requestLeave()), [leaves, requestLeave]);

  useBlocker({
    shouldBlockFn,
    enableBeforeUnload: isDirty && !readOnly,
    disabled: readOnly || !isDirty,
  });
  return requestLeave;
}
