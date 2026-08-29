import type { ReactElement } from "react";

import { Switch } from "../ui/switch";
import { cn } from "../lib/cn";
import { widgetLabel } from "./label";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

function SwitchEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<boolean>): ReactElement {
  return (
    <Switch
      checked={Boolean(value)}
      disabled={readOnly}
      aria-label={widgetLabel(field, "Toggle")}
      onCheckedChange={(checked) => onChange?.(checked)}
    />
  );
}

function SwitchRead({
  value,
}: WidgetRenderProps<boolean>): ReactElement {
  return (
    <span className={cn("text-13", value ? "text-fg" : "text-fg-muted")}>
      {value ? "On" : "Off"}
    </span>
  );
}

export const switchWidget = {
  edit: SwitchEdit,
  read: SwitchRead,
  cell: SwitchRead,
} satisfies WidgetDefinition<boolean>;

export const booleanToggleWidget = {
  edit: SwitchEdit,
  read: SwitchRead,
  cell: SwitchRead,
} satisfies WidgetDefinition<boolean>;
