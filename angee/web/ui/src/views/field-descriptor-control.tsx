import * as React from "react";

import {
  useResolvedWidget,
  type WidgetControlProps,
  type WidgetField,
} from "../widgets";
import {
  fieldWidgetId,
  type FieldDescriptor,
} from "./page";

export interface FieldDescriptorControlProps {
  field: FieldDescriptor & { rowTemplate?: readonly WidgetField[] };
  value: unknown;
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
  readOnly,
  onChange,
  controlProps,
}: FieldDescriptorControlProps): React.ReactElement {
  const widget = useResolvedWidget(fieldWidgetId(field)) ?? fallbackWidget();
  const Component = readOnly ? widget.read : (widget.edit ?? widget.read);
  const widgetField: WidgetField = {
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
      readOnly={readOnly}
      onChange={onChange}
    />
  );
}

function fallbackWidget() {
  return {
    read: ({ value }: { value?: unknown }) => (
      <span className="text-13 text-fg">{String(value ?? "")}</span>
    ),
    edit: ({
      value,
      onChange,
      readOnly,
      field,
    }: {
      value?: unknown;
      onChange?: (value: string) => void;
      readOnly?: boolean;
      field?: WidgetField;
    }) => (
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
