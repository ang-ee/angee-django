import * as React from "react";
import {
  FormRoot,
  RelationFieldWidget,
  relationValueId,
  type WidgetDefinition,
  type WidgetRenderProps,
} from "@angee/ui";

import { usePartiesT } from "./i18n";

export interface PartyPickerProps {
  value?: string | null;
  onChange: (value: string) => void;
  label?: React.ReactNode;
  readOnly?: boolean;
}

function PartyPickerWidget({ value, onChange, onCommit, readOnly, field }: WidgetRenderProps<unknown>): React.ReactElement {
  return <PartyPicker value={relationValueId(value)}
    label={field?.label} readOnly={readOnly} onChange={(next) => { onChange?.(next); onCommit?.(); }} />;
}

export const partyPickerWidget = {
  edit: PartyPickerWidget,
  read: (props) => <PartyPickerWidget {...props} readOnly />,
} satisfies WidgetDefinition<unknown>;

const PARTY = { resource: "parties.Party", labelField: "display_name", canCreate: false };

/** Canonical Party selection; its create makes one of the Party kinds through the shared kind switcher. */
export function PartyPicker({ value, onChange, label, readOnly }: PartyPickerProps): React.ReactElement {
  const t = usePartiesT();
  const labelId = React.useId();
  return <FormRoot.Field label={label ?? t("partyPicker.label")} labelProps={{ id: labelId }}>
    <RelationFieldWidget aria-labelledby={labelId} relation={PARTY} value={value} readOnly={readOnly} onChange={onChange} />
  </FormRoot.Field>;
}
