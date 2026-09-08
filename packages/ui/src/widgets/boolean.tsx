import type { ReactElement } from "react";

import { Checkbox } from "../ui/checkbox";
import { widgetLabel } from "./label";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

function BooleanEdit({
  value,
  onChange,
  onCommit,
  field,
  readOnly,
  controlRef,
}: WidgetRenderProps<boolean>): ReactElement {
  return (
    <Checkbox
      ref={controlRef}
      checked={Boolean(value)}
      disabled={readOnly}
      aria-label={widgetLabel(field, "Boolean")}
      onCheckedChange={(checked) => { onChange?.(checked); onCommit?.(); }}
    />
  );
}

function BooleanRead({
  value,
  field,
}: WidgetRenderProps<boolean>): ReactElement {
  return (
    <Checkbox
      checked={Boolean(value)}
      disabled
      aria-label={widgetLabel(field, "Boolean")}
    />
  );
}

export const booleanWidget = {
  edit: BooleanEdit,
  read: BooleanRead,
  cell: BooleanRead,
} satisfies WidgetDefinition<boolean>;
