import * as React from "react";
import { useBlocker } from "@tanstack/react-router";

import { useConfirm } from "../feedback";
import { useUiT } from "../i18n";

/** Owns both in-app and before-unload protection for a dirty editable form. */
export function useUnsavedChangesNavigationGuard({
  isDirty,
  isDirtyNow,
  readOnly,
}: {
  isDirty: boolean;
  isDirtyNow: () => boolean;
  readOnly: boolean;
}): void {
  const confirm = useConfirm();
  const t = useUiT();
  const shouldBlockFn = React.useCallback(async () => {
    // Read the live store, not the captured render value. A successful save resets
    // the form (isDirty → false) and navigates in the same tick, before the React
    // re-render flushes — a stale `true` here would wrongly block the post-save
    // redirect with a phantom "unsaved changes" prompt.
    if (readOnly || !isDirtyNow()) return false;
    const leave = await confirm({
      title: t("form.unsavedLeaveTitle"),
      cancel: t("form.stay"),
      confirm: t("form.leave"),
      danger: true,
    });
    return !leave;
  }, [confirm, isDirtyNow, readOnly, t]);

  useBlocker({
    shouldBlockFn,
    enableBeforeUnload: isDirty && !readOnly,
    disabled: readOnly || !isDirty,
  });
}
