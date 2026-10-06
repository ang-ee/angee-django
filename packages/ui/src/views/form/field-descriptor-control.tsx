import * as React from "react";
import { useModelMetadata, useSchemaFieldMetadata } from "@angee/metadata";

import {
  useResolvedWidget,
  type WidgetControlProps,
  type WidgetDefinition,
  type WidgetField,
  type WidgetRenderProps,
  type WidgetFocusTarget,
  relationValueId,
} from "../../widgets";
import {
  fieldsWithMetadataDefaults,
  relationFieldInfoForDescriptor,
  relationListFieldInfo,
} from "../resource/model-metadata-defaults";
import { RelationFieldWidget } from "../relation/RelationFieldWidget";
import { RelationMultiFieldWidget } from "../relation/RelationMultiFieldWidget";
import { textWidget } from "../../widgets/text";
import { useUiT } from "../../i18n";
import {
  fieldWidgetId,
  isRelationListField,
  type FieldDescriptor,
} from "../page";
import type { FormSpecFieldDescriptor } from "./form-spec";
import { useFieldValidationRegistration } from "./ActionFormProvider";
import { isCompositeFieldDescriptor } from "./form-view-model";

type DescriptorWidgetField = WidgetField & {
  rowTemplate?: readonly FormSpecFieldDescriptor[];
  objectTemplate?: readonly FormSpecFieldDescriptor[];
  itemTemplate?: FormSpecFieldDescriptor;
  addLabel?: string;
  removeLabel?: string;
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
  /** Resolve the field's standard form widget from this model's metadata. */
  resource?: string;
  /** Hasura condition narrowing a metadata-backed relation's server search. */
  where?: Record<string, unknown>;
  field: FieldDescriptor & {
    rowTemplate?: readonly FormSpecFieldDescriptor[];
    objectTemplate?: readonly FormSpecFieldDescriptor[];
    itemTemplate?: FormSpecFieldDescriptor;
    addLabel?: string;
    removeLabel?: string;
  };
  value: unknown;
  /** Source row for widgets whose display depends on a sibling field (money). */
  row?: unknown;
  parentRow?: unknown;
  messages?: readonly string[];
  readOnly?: boolean;
  /** Temporarily lock the mounted editor without replacing its draft with a read view. */
  disabled?: boolean;
  onChange?: (value: unknown) => void;
  onRowChange?: (patch: Record<string, unknown>) => void;
  onCommit?: () => void;
  onValidityChange?: (valid: boolean) => void;
  controlProps?: WidgetControlProps;
  controlRef?: (target: WidgetFocusTarget | null) => void;
}

/**
 * Render one declarative page field through the widget registry. FormView and
 * dialog forms share this owner so field descriptors resolve widgets, labels,
 * and options the same way wherever an addon renders mutation inputs.
 */
export function FieldDescriptorControl(props: FieldDescriptorControlProps): React.ReactElement {
  const model = useModelMetadata(props.resource ?? "");
  const schema = useSchemaFieldMetadata();
  const field = props.resource ? { ...props.field, ...fieldsWithMetadataDefaults([props.field], model, schema)[0] } : props.field;
  const relation = props.resource ? relationFieldInfoForDescriptor(field, model, schema) : null;
  const relationList = props.resource && isRelationListField(field) ? relationListFieldInfo(field.name, model, schema) : null;
  const widgetId = fieldWidgetId(field);
  const widget = useResolvedWidget(widgetId) ?? FALLBACK_TEXT_WIDGET;
  const ariaLabel = typeof field.label === "string" ? field.label : field.name;
  if (relation && (!field.widget || field.widget === "many2one")) return <RelationFieldWidget
    value={relationValueId(props.value) || null} onChange={props.onChange} onCommit={props.onCommit}
    readOnly={props.readOnly || props.disabled} relation={relation} filters={field.filters} where={props.where}
    aria-label={ariaLabel} controlRef={props.controlRef} controlProps={props.controlProps} />;
  if (relationList) return <RelationMultiFieldWidget
    value={Array.isArray(props.value) ? props.value : []} onChange={props.onChange} onCommit={props.onCommit}
    readOnly={props.readOnly || props.disabled} relation={relationList} filters={field.filters} where={props.where}
    aria-label={ariaLabel} controlRef={props.controlRef} controlProps={props.controlProps} />;
  const Component = props.readOnly ? widget.read : (widget.edit ?? widget.read);
  const mode = Component === widget.read ? "read" : "edit";
  // Draft validity belongs to the mounted editor; temporary disabling keeps it.
  return <FieldDescriptorControlInstance key={`${widgetId}:${mode}`} {...props} field={field} Component={Component} />;
}

function FieldDescriptorControlInstance({
  Component,
  field,
  value,
  row,
  parentRow,
  messages,
  readOnly,
  disabled,
  onChange,
  onRowChange,
  onCommit,
  onValidityChange,
  controlProps,
  controlRef,
}: FieldDescriptorControlProps & { Component: WidgetDefinition["read"] }): React.ReactElement {
  const t = useUiT();
  const valid = React.useRef(true);
  const registerFieldValidation = useFieldValidationRegistration();
  React.useEffect(() => registerFieldValidation?.(field.name, () =>
    readOnly || valid.current ? undefined : t("form.invalidValue")), [field.name, readOnly, registerFieldValidation, t]);
  const reportValidity = React.useCallback((next: boolean) => {
    valid.current = next;
    onValidityChange?.(next);
  }, [onValidityChange]);
  const widgetField: DescriptorWidgetField = {
    name: field.name,
    fill: field.fill,
    containerWidth: field.containerWidth,
    visibilityAction: field.visibilityAction,
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
    ...(field.addLabel ? { addLabel: field.addLabel } : {}),
    ...(field.removeLabel ? { removeLabel: field.removeLabel } : {}),
    ...(field.minItems !== undefined ? { minItems: field.minItems } : {}),
    ...(field.maxItems !== undefined ? { maxItems: field.maxItems } : {}),
    ...(field.currencyField ? { currencyField: field.currencyField } : {}),
  };
  return (
    <Component
      value={field.valueCodec ? field.valueCodec.toControl(value) : value}
      row={row}
      parentRow={parentRow}
      field={widgetField}
      messages={messages}
      readOnly={readOnly || (disabled && !isCompositeFieldDescriptor(field))}
      disabled={disabled}
      onChange={onChange && ((next) => onChange(field.valueCodec ? field.valueCodec.fromControl(next) : next))}
      onRowChange={onRowChange}
      onCommit={onCommit}
      onValidityChange={reportValidity}
      controlRef={controlRef}
    />
  );
}
