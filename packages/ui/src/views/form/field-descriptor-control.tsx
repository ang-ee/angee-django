import * as React from "react";

import {
  useResolvedWidget,
  type WidgetControlProps,
  type WidgetDefinition,
  type WidgetField,
  type WidgetRenderProps,
  type WidgetFocusTarget,
} from "../../widgets";
import { textWidget } from "../../widgets/text";
import {
  fieldWidgetId,
  type FieldDescriptor,
} from "../page";
import type { FormSpecFieldDescriptor } from "./form-spec";

type DescriptorWidgetField = WidgetField & {
  rowTemplate?: readonly FormSpecFieldDescriptor[];
  objectTemplate?: readonly FormSpecFieldDescriptor[];
  itemTemplate?: FormSpecFieldDescriptor;
  minItems?: number;
  maxItems?: number;
};

function FallbackTextRead(props: WidgetRenderProps): React.ReactElement {
  const Component = textWidget.read;
  return <Component {...props} value={String(props.value ?? "")} />;
}

function FallbackTextEdit(props: WidgetRenderProps): React.ReactElement {
  const Component = textWidget.edit;
  return <Component {...props} value={String(props.value ?? "")} />;
}

const FALLBACK_TEXT_WIDGET: WidgetDefinition = {
  read: FallbackTextRead,
  edit: FallbackTextEdit,
};

export interface FieldDescriptorControlProps {
  field: FieldDescriptor & {
    rowTemplate?: readonly FormSpecFieldDescriptor[];
    objectTemplate?: readonly FormSpecFieldDescriptor[];
    itemTemplate?: FormSpecFieldDescriptor;
  };
  value: unknown;
  /** Source row for widgets whose display depends on a sibling field (money). */
  row?: unknown;
  parentRow?: unknown;
  messages?: readonly string[];
  readOnly?: boolean;
  onChange?: (value: unknown) => void;
  onRowChange?: (patch: Record<string, unknown>) => void;
  onCommit?: () => void;
  controlProps?: WidgetControlProps;
  controlRef?: (target: WidgetFocusTarget | null) => void;
}

/**
 * Render one declarative page field through the widget registry. FormView and
 * dialog forms share this owner so field descriptors resolve widgets, labels,
 * and options the same way wherever an addon renders mutation inputs.
 */
export function FieldDescriptorControl({
  field,
  value,
  row,
  parentRow,
  messages,
  readOnly,
  onChange,
  onRowChange,
  onCommit,
  controlProps,
  controlRef,
}: FieldDescriptorControlProps): React.ReactElement {
  const widget = useResolvedWidget(fieldWidgetId(field))
    ?? FALLBACK_TEXT_WIDGET;
  const Component = readOnly ? widget.read : (widget.edit ?? widget.read);
  const widgetField: DescriptorWidgetField = {
    name: field.name,
    label: field.label,
    options: field.options,
    placeholder: field.placeholder,
    ...(controlProps ? { controlProps: {
        ...controlProps,
        ...(field.minimum !== undefined ? { min: field.minimum } : {}),
        ...(field.maximum !== undefined ? { max: field.maximum } : {}),
        ...(field.minLength !== undefined ? { minLength: field.minLength } : {}),
        ...(field.maxLength !== undefined ? { maxLength: field.maxLength } : {}),
      } } : {}),
    ...(field.rowTemplate ? { rowTemplate: field.rowTemplate } : {}),
    ...(field.objectTemplate ? { objectTemplate: field.objectTemplate } : {}),
    ...(field.itemTemplate ? { itemTemplate: field.itemTemplate } : {}),
    ...(field.minItems !== undefined ? { minItems: field.minItems } : {}),
    ...(field.maxItems !== undefined ? { maxItems: field.maxItems } : {}),
    ...(field.currencyField ? { currencyField: field.currencyField } : {}),
  };
  return (
    <Component
      value={value}
      row={row}
      parentRow={parentRow}
      field={widgetField}
      messages={messages}
      readOnly={readOnly}
      onChange={onChange}
      onRowChange={onRowChange}
      onCommit={onCommit}
      controlRef={controlRef}
    />
  );
}
