import * as v from "valibot";
import {
  deserializeFormSpec, formSpecInitialValues, JsonValueSchema, normalizeFormSpecValues,
  parseFormSpec, type DescriptorField, type FormSpecFieldDescriptor, type UiTranslate, type WidgetMap,
} from "@angee/ui";
import { createJsonSchemaAjv, createJsonSchemaResolver } from "@angee/ui/views/json-schema";
import type { Resolver } from "react-hook-form";
import type { DecisionsT } from "./i18n";

export type DecisionValues = Record<string, unknown>;

/** Compose the frozen discriminator and branches through shared form owners. */
export function decisionForm(value: unknown, widgets: WidgetMap, t: DecisionsT, uiT?: UiTranslate) {
  const schema = v.parse(v.record(v.string(), JsonValueSchema), value);
  const spec = parseFormSpec(schema);
  const actionField = spec.properties?.action;
  if (!actionField?.options?.length || !spec.oneOf?.length) throw new Error("Invalid decision form");
  const branches = new Map<unknown, readonly FormSpecFieldDescriptor[]>(spec.oneOf.map((entry) => {
    const { action, ...properties } = entry.properties ?? {};
    return [action?.const, deserializeFormSpec({ ...entry, properties,
      required: entry.required?.filter((name) => name !== "action"),
    }, widgets)];
  }));
  const fields = (action: unknown) => branches.get(action) ?? [];
  const branch: NonNullable<DescriptorField["branchReset"]> = (action) => ({
    fields: fields(action).map((field) => field.name),
    values: formSpecInitialValues(fields(action), {}),
  });
  const actionFields: FormSpecFieldDescriptor[] = deserializeFormSpec({
    properties: { action: { ...actionField, label: t("decision.action") } }, required: ["action"],
  }, widgets).map((field) => ({ ...field, branchReset: branch }));
  const allFields = (action: unknown) => [...actionFields, ...fields(action)];
  const initial = (payload: DecisionValues = {}) => {
    const action = payload.action ?? actionField.options![0]!.value;
    return formSpecInitialValues(allFields(action), { ...payload, action });
  };
  const validate = createJsonSchemaResolver(schema, createJsonSchemaAjv({ strictTuples: false }), undefined, uiT);
  const resolver: Resolver<DecisionValues> = (values, context, options) =>
    validate(normalizeFormSpecValues(allFields(values.action), values), context, options);
  const fieldNames = ["action", ...new Set([...branches.values()].flatMap((branch) => branch.map((field) => field.name)))];
  return { actionFields, fields, initial, resolver, fieldNames };
}
