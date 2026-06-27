import * as React from "react";

import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { textRoleVariants } from "../ui/text";

export interface EditorHeaderBarProps {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  tags?: readonly React.ReactNode[];
  onCancel?: () => void;
  onSubmit?: () => void;
  saving?: boolean;
  submitLabel?: React.ReactNode;
  cancelLabel?: React.ReactNode;
  submitDisabled?: boolean;
  actions?: React.ReactNode;
}

export function EditorHeaderBar({
  title,
  subtitle,
  tags = [],
  onCancel,
  onSubmit,
  saving = false,
  submitLabel = "Save",
  cancelLabel = "Cancel",
  submitDisabled = false,
  actions,
}: EditorHeaderBarProps): React.ReactElement {
  return (
    <>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h1 className="min-w-0 truncate text-lg font-semibold text-fg">
            {title}
          </h1>
          {tags.map((tag, index) => (
            <Badge key={index}>{tag}</Badge>
          ))}
        </div>
        {subtitle ? <p className={textRoleVariants({ role: "description" })}>{subtitle}</p> : null}
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {actions}
        {onCancel ? (
          <Button type="button" variant="secondary" onClick={onCancel}>
            {cancelLabel}
          </Button>
        ) : null}
        {onSubmit ? (
          <Button
            disabled={submitDisabled}
            loading={saving}
            onClick={onSubmit}
            type="button"
            variant="primary"
          >
            {submitLabel}
          </Button>
        ) : null}
      </div>
    </>
  );
}

