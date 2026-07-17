import * as React from "react";

import {
  useResolvedWidget,
  type WidgetControlProps,
  type WidgetDefinition,
  type WidgetField,
  type WidgetRenderProps,
} from "../widgets";
import {
  fieldWidgetId,
  type FieldDescriptor,
} from "./page";
import type { FormSpecFieldDescriptor } from "./form-spec";

type DescriptorWidgetField = WidgetField & {
  rowTemplate?: readonly FormSpecFieldDescriptor[];
};

export interface FieldDescriptorControlProps {
  field: FieldDescriptor & {
    rowTemplate?: readonly FormSpecFieldDescriptor[];
  };
  value: unknown;
  messages?: readonly string[];
  readOnly?: boolean;
  onChange?: (value: unknown) => void;
  controlProps?: WidgetControlProps;
}

/**
 * Render one declarative page field through the widget registry. FormView and
 * dialog forms share this owner so field descriptors resolve widgets, labels,
 * and options the same way wherever an addon renders mutation inputs.
 */
export function FieldDescriptorControl({
  field,
  value,
  messages,
  readOnly,
  onChange,
  controlProps,
}: FieldDescriptorControlProps): React.ReactElement {
  const widget = useResolvedWidget(fieldWidgetId(field)) ?? fallbackWidget();
  const Component = readOnly ? widget.read : (widget.edit ?? widget.read);
  const widgetField: DescriptorWidgetField = {
    name: field.name,
    label: field.label,
    options: field.options,
    placeholder: field.placeholder,
    controlProps,
    ...(field.rowTemplate ? { rowTemplate: field.rowTemplate } : {}),
    ...(field.currencyField ? { currencyField: field.currencyField } : {}),
  };
  return (
    <Component
      value={value}
      field={widgetField}
      messages={messages}
      readOnly={readOnly}
      onChange={onChange}
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
      readOnly,
      field,
    }: WidgetRenderProps) => (
      <input
        {...field?.controlProps}
        className="h-9 w-full rounded-6 border border-border bg-sheet px-3 text-13 text-fg"
        value={String(value ?? "")}
        readOnly={readOnly}
        onChange={(event) => onChange?.(event.currentTarget.value)}
      />
    ),
  };
}
