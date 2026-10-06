import type { ReactElement, ReactNode } from "react";

import { isTone, type Tone } from "../lib/tones";
import { Badge } from "../ui/badge";
import { selectWidget } from "./select";
import { optionLabel, optionToken, type WidgetDefinition, type WidgetRenderProps } from "./types";

/** The palette a tone value names; an unknown value reads as neutral. */
function toneOf(value: unknown): Tone {
  const token = optionToken(value);
  return isTone(token) ? token : "neutral";
}

function ToneChip({ value, children }: { value: unknown; children: ReactNode }): ReactElement {
  return <Badge tone={toneOf(value)} density="compact" shape="pill">{children}</Badge>;
}

function ToneRead({ value, field }: WidgetRenderProps<string>): ReactElement {
  if (!value) return <span>—</span>;
  return <ToneChip value={value}>{optionLabel(field?.options, value)}</ToneChip>;
}

/** The shared select, its choices shown as chips in their own tone. */
function ToneEdit(props: WidgetRenderProps<string>): ReactElement {
  const SelectEdit = selectWidget.edit;
  const options = props.field?.options?.map((option) => ({
    ...option,
    label: <ToneChip value={option.value}>{option.label}</ToneChip>,
  }));
  return <SelectEdit {...props} field={{ ...props.field, ...(options ? { options } : {}) }} />;
}

/**
 * A field whose values are the tone vocabulary itself (a stage's colour): each value
 * renders as a chip in its own tone, not as a status mapped through `STATUS_TONES`.
 * The backend's `ToneField` declares it.
 */
export const toneWidget = {
  edit: ToneEdit,
  read: ToneRead,
  cell: ToneRead,
} satisfies WidgetDefinition<string>;
