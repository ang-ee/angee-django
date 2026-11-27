import * as React from "react";
import {
  FormRoot,
  RelationFieldWidget,
  relationSelectedOption,
  relationValueId,
  widgetLabel,
  type RelationFieldWidgetProps,
  type WidgetDefinition,
  type WidgetRenderProps,
} from "@angee/ui";

import { usePartiesT } from "./i18n";

export interface PartyPickerProps extends Pick<RelationFieldWidgetProps, "edit" | "selectedOption"> {
  value?: string | null;
  onChange: (value: string) => void;
  label?: React.ReactNode;
  readOnly?: boolean;
}

/**
 * The registered widget is a form field's control: the field around it owns the label and the
 * invalid state. It is named as the framework's relation widgets are (`widgetLabel`): by the
 * label a group's control props point at (`aria-labelledby`), else by the field's text label,
 * else "Party".
 */
function PartyPickerWidget({ value, onChange, onCommit, readOnly, field, controlRef }: WidgetRenderProps<unknown>): React.ReactElement {
  const t = usePartiesT();
  return <RelationFieldWidget relation={PARTY} value={relationValueId(value)} selectedOption={relationSelectedOption(value, PARTY.labelField)}
    edit={field?.relationEdit} readOnly={readOnly} controlRef={controlRef} controlProps={field?.controlProps}
    aria-labelledby={field?.controlProps?.["aria-labelledby"]} aria-label={widgetLabel(field, t("partyPicker.label"))}
    onChange={(next) => { onChange?.(next); onCommit?.(); }} />;
}

export const partyPickerWidget = {
  edit: PartyPickerWidget,
  read: (props) => <PartyPickerWidget {...props} readOnly />,
} satisfies WidgetDefinition<unknown>;

const PARTY = { resource: "parties.Party", labelField: "display_name", canCreate: false };

/** Canonical Party selection; its create makes one of the Party kinds through the shared kind switcher. */
export function PartyPicker({ value, onChange, label, readOnly, edit, selectedOption }: PartyPickerProps): React.ReactElement {
  const t = usePartiesT();
  const labelId = React.useId();
  return <FormRoot.Field label={label ?? t("partyPicker.label")} labelProps={{ id: labelId }}>
    <RelationFieldWidget aria-labelledby={labelId} relation={PARTY} value={value} selectedOption={selectedOption} edit={edit}
      readOnly={readOnly} onChange={onChange} />
  </FormRoot.Field>;
}
