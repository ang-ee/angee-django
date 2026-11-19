import * as React from "react";
import { ActionFormDialog, DropdownMenu, Glyph, useRuntimeAuth, useRuntimeViewAs } from "@angee/ui";

import { useChangePasswordAction } from "./account-actions";

/** The personal password form, contributed to the shared user menu. */
export function ChangePasswordMenuItem(): React.ReactElement | null {
  const [open, setOpen] = React.useState(false);
  const action = useChangePasswordAction();
  const preview = useRuntimeViewAs();
  const { user } = useRuntimeAuth();
  if (user?.hasUsablePassword === false) return null;
  return <>
    <DropdownMenu.Item aria-haspopup="dialog" disabled={Boolean(preview.viewAs || preview.pending)}
      onClick={() => setOpen(true)}>
      <Glyph name="auth" /><span className="flex-1 truncate">{action.label}</span>
    </DropdownMenu.Item>
    <ActionFormDialog action={action} context={{ record: null, selectedIds: [] }} open={open} onOpenChange={setOpen} />
  </>;
}
