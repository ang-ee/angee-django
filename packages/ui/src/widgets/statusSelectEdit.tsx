import type { ReactElement } from "react";

import { Select } from "../ui/select";
import { widgetLabel } from "./label";
import type { WidgetRenderProps } from "./types";

export function StatusSelectEdit({
  value,
  onChange,
  onCommit,
  field,
  readOnly,
  controlRef,
}: WidgetRenderProps<string>): ReactElement {
  return (
    <Select
      triggerRef={controlRef}
      value={value ?? ""}
      options={field?.options ?? []}
      readOnly={readOnly}
      disabled={readOnly}
      aria-label={widgetLabel(field, "Status")}
      placeholder={widgetLabel(field, "Status")}
      onValueChange={(next) => {
        onChange?.(next);
        onCommit?.();
      }}
    />
  );
}
