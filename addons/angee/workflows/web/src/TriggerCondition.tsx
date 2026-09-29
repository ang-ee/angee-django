import * as v from "valibot";
import { ResourceConditionEditor, optionToken, useImplChoices, type WidgetRenderProps } from "@angee/ui";
import { TRIGGER_MODEL } from "./triggers";

const TriggerFields = v.object({ source: v.optional(v.string()), model_label: v.optional(v.string()), source_model: v.optional(v.string()) });
const SourceDefaults = v.object({ source_model: v.optional(v.string(), "") });

/** Source metadata supplies fixed models; only generic sources use the authored model label. */
export function TriggerCondition({ value, row, readOnly, disabled, onChange }: WidgetRenderProps) {
  const choices = useImplChoices(TRIGGER_MODEL, "source", readOnly || disabled ? [] : undefined);
  const fields = v.safeParse(TriggerFields, row);
  const choice = choices.find(({ key }) => key === optionToken(fields.success ? fields.output.source : ""));
  const defaults = v.safeParse(SourceDefaults, choice?.defaults);
  const model = defaults.success && defaults.output.source_model
    || (fields.success ? fields.output.model_label || fields.output.source_model : "") || "";
  return <ResourceConditionEditor resource={model} value={value} onChange={onChange} readOnly={readOnly || disabled} />;
}
