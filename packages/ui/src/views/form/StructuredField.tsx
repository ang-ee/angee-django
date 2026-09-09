/** Recursive controlled FormSpec object and variable-list widgets. */
import * as React from "react";

import { Button } from "../../ui/button";
import { useUiT } from "../../i18n";
import type { WidgetDefinition, WidgetField, WidgetRenderProps } from "../../widgets/types";
import type { FormSpecFieldDescriptor } from "./form-spec";
import { initialFormSpecValue } from "./form-spec";
import { LabeledDescriptorField } from "./MutationDialog";
import { messagesForDottedPath } from "./validation-errors";

type StructuredWidgetField = WidgetField & {
  objectTemplate?: readonly FormSpecFieldDescriptor[];
  itemTemplate?: FormSpecFieldDescriptor;
  minItems?: number;
  maxItems?: number;
};

function ObjectField({ value, field, messages = [], readOnly = false, onChange, onCommit, controlRef }: WidgetRenderProps): React.ReactElement {
  const template = structuredField(field).objectTemplate;
  if (!template) throw new Error('The "object" widget requires field.objectTemplate.');
  const objectValue = recordValue(value);
  const name = requiredName(field, "object");
  const focusIndex = template.findIndex((child) => !child.readOnly);
  return (
    <fieldset id={field?.controlProps?.id} aria-labelledby={field?.controlProps?.["aria-labelledby"]} aria-describedby={field?.controlProps?.["aria-describedby"]} className="space-y-3 rounded-6 border border-border p-3">
      {template.map((child, index) => {
        const path = `${name}.${child.name}`;
        return <LabeledDescriptorField key={child.name} field={{ ...child, name: path }}
          value={objectValue[child.name]} messages={messagesForDottedPath(messages, path)}
          readOnly={readOnly || child.readOnly}
          controlRef={index === focusIndex ? controlRef : undefined}
          onCommit={onCommit}
          onChange={(next) => onChange?.(updatedRecord(objectValue, child.name, next))} />;
      })}
    </fieldset>
  );
}

function ListField({ value, field, messages = [], readOnly = false, onChange, onCommit, controlRef }: WidgetRenderProps): React.ReactElement {
  const t = useUiT();
  const descriptorField = structuredField(field);
  const item = descriptorField.itemTemplate;
  if (!item) throw new Error('The "list" widget requires field.itemTemplate.');
  if (value != null && !Array.isArray(value)) throw new Error('The "list" widget value must be an array.');
  const values = Array.isArray(value) ? value : [];
  const name = requiredName(field, "list");
  const [identities, nextIdentity] = useListIdentities(values.length);
  return (
    <div id={field?.controlProps?.id} className="space-y-3" aria-labelledby={field?.controlProps?.["aria-labelledby"]} aria-describedby={field?.controlProps?.["aria-describedby"]}>
      {values.map((entry, index) => {
        const path = `${name}.${index}`;
        return (
          <div key={identities.current[index]} className="space-y-2 rounded-6 border border-border p-3">
            <LabeledDescriptorField field={{ ...item, name: path, label: item.label ?? t("form.list.item", { number: index + 1 }) }}
              value={entry} messages={messagesForDottedPath(messages, path)} readOnly={readOnly || item.readOnly}
              controlRef={index === 0 ? controlRef : undefined}
              onCommit={onCommit}
              onChange={(next) => onChange?.(values.map((current, currentIndex) => currentIndex === index ? next : current))} />
            {!readOnly ? <div className="flex flex-wrap gap-1">
              <Button type="button" size="sm" variant="ghost" disabled={index === 0} aria-label={t("form.list.moveUpNamed", { number: index + 1 })}
                onClick={() => { identities.current = moved(identities.current, index, index - 1); onChange?.(moved(values, index, index - 1)); onCommit?.(); }}>{t("form.list.moveUp")}</Button>
              <Button type="button" size="sm" variant="ghost" disabled={index === values.length - 1} aria-label={t("form.list.moveDownNamed", { number: index + 1 })}
                onClick={() => { identities.current = moved(identities.current, index, index + 1); onChange?.(moved(values, index, index + 1)); onCommit?.(); }}>{t("form.list.moveDown")}</Button>
              <Button type="button" size="sm" variant="ghost" disabled={descriptorField.minItems !== undefined && values.length <= descriptorField.minItems} aria-label={t("form.list.removeNamed", { number: index + 1 })}
                onClick={() => { identities.current.splice(index, 1); onChange?.(values.filter((_, currentIndex) => currentIndex !== index)); onCommit?.(); }}>{t("form.list.remove")}</Button>
            </div> : null}
          </div>
        );
      })}
      {!readOnly ? <Button ref={values.length === 0 ? controlRef : undefined} type="button" size="sm" variant="secondary" disabled={descriptorField.maxItems !== undefined && values.length >= descriptorField.maxItems}
        onClick={() => { identities.current.push(nextIdentity()); onChange?.([...values, initialFormSpecValue(item)]); onCommit?.(); }}>{t("form.list.add")}</Button> : null}
    </div>
  );
}

function structuredField(field: WidgetField | undefined): StructuredWidgetField {
  return (field ?? {}) as StructuredWidgetField;
}

function requiredName(field: WidgetField | undefined, widget: string): string {
  if (!field?.name) throw new Error(`The "${widget}" widget requires a descriptor field name.`);
  return field.name;
}

function recordValue(value: unknown): Record<string, unknown> {
  if (value == null) return {};
  if (typeof value !== "object" || Array.isArray(value)) throw new Error('The "object" widget value must be an object.');
  return value as Record<string, unknown>;
}

function updatedRecord(value: Record<string, unknown>, key: string, next: unknown): Record<string, unknown> {
  if (next !== undefined) return { ...value, [key]: next };
  const updated = { ...value };
  delete updated[key];
  return updated;
}

function moved<T>(values: readonly T[], from: number, to: number): T[] {
  const updated = [...values];
  const [entry] = updated.splice(from, 1);
  updated.splice(to, 0, entry as T);
  return updated;
}

/** Stable client-only identities for controlled list rows across moves/removal. */
export function useListIdentities(length: number): [React.MutableRefObject<string[]>, () => string] {
  const prefix = React.useId();
  const counter = React.useRef(0);
  const identities = React.useRef<string[]>([]);
  const next = React.useCallback(() => {
    counter.current += 1;
    return `${prefix}-${counter.current}`;
  }, [prefix]);
  while (identities.current.length < length) identities.current.push(next());
  if (identities.current.length > length) identities.current.length = length;
  return [identities, next];
}

export const objectWidget = { edit: ObjectField, read: (props: WidgetRenderProps) => <ObjectField {...props} readOnly /> } satisfies WidgetDefinition;
export const listWidget = { edit: ListField, read: (props: WidgetRenderProps) => <ListField {...props} readOnly /> } satisfies WidgetDefinition;
