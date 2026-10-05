import type * as React from "react";

import { useUiT } from "../../i18n";
import { Button } from "../../ui/button";

/** Tint for a control band containing unsaved edits. */
export const dirtyControlBandClassName = "bg-brand-soft";

export interface SaveDiscardActionsProps {
  isDirty: boolean;
  pending?: boolean;
  /** Block saving while leaving Discard available unless pending. */
  saveDisabled?: boolean;
  /** Keep Save visible even before any edits, for example on creation. */
  alwaysShowSave?: boolean;
  /** Select the default translated label without knowing what is being saved. */
  saveIntent?: "save" | "create";
  saveLabel?: React.ReactNode;
  onDiscard: () => void;
  onSave: () => void;
}

/** Standard control-band actions; callers own draft state and persistence. */
export function SaveDiscardActions({
  isDirty,
  pending = false,
  saveDisabled = false,
  alwaysShowSave = false,
  saveIntent = "save",
  saveLabel,
  onDiscard,
  onSave,
}: SaveDiscardActionsProps): React.ReactElement | null {
  const t = useUiT();
  if (!isDirty && !alwaysShowSave) return null;

  return (
    <div className="flex items-center gap-2">
      {isDirty ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={pending}
          onClick={onDiscard}
        >
          {t("form.discard")}
        </Button>
      ) : null}
      <Button
        type="button"
        variant="primary"
        size="sm"
        loading={pending}
        disabled={saveDisabled}
        onClick={onSave}
      >
        {saveLabel ?? t(saveIntent === "create" ? "form.create" : "form.save")}
      </Button>
    </div>
  );
}
