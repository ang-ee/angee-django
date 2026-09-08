import * as React from "react";

import {
  useResolvedWidget,
  type WidgetControlProps,
  type WidgetDefinition,
  type WidgetField,
  type WidgetRenderProps,
  type WidgetFocusTarget,
} from "../../widgets";
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

export interface FieldDescriptorControlProps {
  field: FieldDescriptor & {
    rowTemplate?: readonly FormSpecFieldDescriptor[];
    objectTemplate?: readonly FormSpecFieldDescriptor[];
    itemTemplate?: FormSpecFieldDescriptor;
  };
  value: unknown;
  /** Source row for widgets whose display depends on a sibling field (money). */
  row?: unknown;
  messages?: readonly string[];
  readOnly?: boolean;
  onChange?: (value: unknown) => void;
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
  messages,
  readOnly,
  onChange,
  onCommit,
  controlProps,
  controlRef,
}: FieldDescriptorControlProps): React.ReactElement {
  const widget = useResolvedWidget(fieldWidgetId(field)) ?? fallbackWidget();
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
      field={widgetField}
      messages={messages}
      readOnly={readOnly}
      onChange={onChange}
      onCommit={onCommit}
      controlRef={controlRef}
    />
  );
}

function fallbackWidget(): WidgetDefinition {
  return {
    read: ({ value }: WidgetRenderProps) => (
      <span className="text-13 text-fg">{String(value ?? "")}</span>
    ),
    edit: ({
      value,
      onChange,
      onCommit,
      readOnly,
      field,
      controlRef,
    }: WidgetRenderProps) => (
      <input
        {...field?.controlProps}
        ref={controlRef}
        className="h-9 w-full rounded-6 border border-border bg-sheet px-3 text-13 text-fg"
        value={String(value ?? "")}
        readOnly={readOnly}
        onChange={(event) => onChange?.(event.currentTarget.value)}
        onBlur={onCommit}
      />
    ),
  };
}
