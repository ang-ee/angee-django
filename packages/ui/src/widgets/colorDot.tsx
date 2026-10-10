import type { ReactElement } from "react";

import { statusBadgeWidget } from "./statusBadge";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

/**
 * Compatibility name for status fields that historically selected the dot shape.
 * Rendering and editing stay owned by `statusBadge`; an explicit `statusDisplay`
 * still wins when a caller is migrating this alias.
 */
function ColorDotRead({ field, ...props }: WidgetRenderProps<string>): ReactElement {
  const Read = statusBadgeWidget.read;
  return <Read {...props} field={{ ...field, statusDisplay: field?.statusDisplay ?? "dot" }} />;
}

/** @deprecated Use `statusBadgeWidget` with `statusDisplay: "dot"`. */
export const colorDotWidget = {
  edit: statusBadgeWidget.edit,
  read: ColorDotRead,
  cell: ColorDotRead,
} satisfies WidgetDefinition<string>;
