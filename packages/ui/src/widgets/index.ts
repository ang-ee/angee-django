import { useWidget, type WidgetMap } from "../runtime";

import { booleanBadgeWidget } from "./booleanBadge";
import { booleanWidget } from "./boolean";
import { colorDotWidget } from "./colorDot";
import { comboboxWidget } from "./combobox";
import { dateWidget } from "./date";
import { datetimeWidget } from "./datetime";
import { jsonWidget } from "./json";
import { markdownEditorWidget, markdownPreviewWidget } from "./markdown";
import { many2manyWidget } from "./many2many";
import { many2oneWidget } from "./many2one";
import { floatWidget, integerWidget } from "./number";
import { ownerCellWidget } from "./ownerCell";
import { progressBarWidget } from "./progressBar";
import { ribbonWidget } from "./ribbon";
import { emailWidget, phoneWidget, urlWidget } from "./scalarText";
import { selectionWidget, selectWidget } from "./select";
import { slugWidget } from "./slug";
import { statusBadgeWidget } from "./statusBadge";
import { statusbarWidget } from "./statusbar";
import { booleanToggleWidget, switchWidget } from "./switch";
import { tagInputWidget } from "./tagInput";
import { textareaWidget } from "./textarea";
import { textWidget } from "./text";
import { themePickerWidget } from "./themePicker";
import { userRefWidget } from "./userRef";
import type { WidgetDefinition } from "./types";

export {
  Markdown,
  WikilinkProvider,
  useWikilinkResolver,
  type WikilinkResolver,
  type WikilinkTarget,
} from "./markdown";

export type {
  WidgetDefinition,
  WidgetField,
  WidgetOption,
  WidgetRenderProps,
} from "./types";
export { widgetLabel } from "./label";
export { slugify } from "./slug";
export { STATUS_TONES, statusTone, type StatusToneOptions } from "./status-tones";
export {
  DATE_DISPLAY_FORMAT,
  DATETIME_DISPLAY_FORMAT,
  DATE_STORAGE_FORMAT,
  DATETIME_STORAGE_FORMAT,
  TIME_INPUT_FORMAT,
  dateFromValue,
  formatDate,
  formatDateStorage,
  formatDateTime,
  formatDateTimeStorage,
  formatTimeInput,
  valueLabel,
  type DateFormatValue,
  type DateWidgetValue,
} from "./date-format";
export {
  RelationField,
  type RelationFieldProps,
  type RelationOption,
} from "./RelationField";

export const defaultWidgets = {
  text: textWidget,
  textarea: textareaWidget,
  integer: integerWidget,
  float: floatWidget,
  email: emailWidget,
  url: urlWidget,
  phone: phoneWidget,
  boolean: booleanWidget,
  booleanBadge: booleanBadgeWidget,
  booleanToggle: booleanToggleWidget,
  colorDot: colorDotWidget,
  date: dateWidget,
  json: jsonWidget,
  datetime: datetimeWidget,
  combobox: comboboxWidget,
  statusBadge: statusBadgeWidget,
  progressBar: progressBarWidget,
  statusbar: statusbarWidget,
  ribbon: ribbonWidget,
  tagInput: tagInputWidget,
  "markdown.editor": markdownEditorWidget,
  "markdown.preview": markdownPreviewWidget,
  select: selectWidget,
  selection: selectionWidget,
  slug: slugWidget,
  switch: switchWidget,
  userRef: userRefWidget,
  ownerCell: ownerCellWidget,
  themePicker: themePickerWidget,
  many2one: many2oneWidget,
  many2many: many2manyWidget,
} satisfies WidgetMap;

export function useResolvedWidget(
  id: string,
): WidgetDefinition | undefined {
  return asWidgetDefinition(useWidget(id));
}

export function asWidgetDefinition(
  value: unknown,
): WidgetDefinition | undefined {
  return isWidgetDefinition(value) ? value : undefined;
}

export function isWidgetDefinition(value: unknown): value is WidgetDefinition {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<PropertyKey, unknown>;
  return (
    typeof record.edit === "function" ||
    typeof record.read === "function" ||
    typeof record.cell === "function"
  );
}
