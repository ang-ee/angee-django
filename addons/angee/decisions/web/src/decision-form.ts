import { deserializeFormSpec, parseFormSpec, type FormSpecFieldDescriptor, type WidgetMap } from "@angee/ui";

export interface DecisionFormAction {
  value: string;
  label: string;
  fields: readonly FormSpecFieldDescriptor[];
}

/** Project the frozen decision branches through the shared FormSpec owner. */
export function decisionFormActions(value: unknown, widgets: WidgetMap): readonly DecisionFormAction[] {
  const schema = parseFormSpec(value);
  const options = schema.properties?.action?.options;
  if (!options?.length || !schema.oneOf) throw new Error("Missing decision action branches.");
  return options.map((option) => {
    const branches = schema.oneOf!.filter((branch) => branch.properties?.action?.const === option.value);
    if (branches.length !== 1) throw new Error("Decision actions need one distinct branch.");
    const fields = deserializeFormSpec({ ...schema, ...branches[0], oneOf: undefined }, widgets)
      .filter((field) => field.name !== "action");
    return { value: option.value, label: option.label, fields };
  });
}
