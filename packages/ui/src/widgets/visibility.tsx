import { rowValueAtPath } from "@angee/metadata";
import { useMutation } from "@tanstack/react-query";
import type { ReactElement } from "react";

import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DropdownMenu } from "../ui/dropdown-menu";
import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { useRuntimeViewAs } from "../runtime";
import { useLatestRef } from "../lib/use-latest-ref";
import { useActionResultRun } from "../views/resource/action-result-run";
import { useRecordChromeActionOutcome } from "../views/resource/record-action";
import { useRecordChromeContextMaybe, useRecordChromeContext } from "../views/resource/record-chrome-context";
import { selectWidget } from "./select";
import { canonicalOptionValue, optionLabel, type VisibilityBinding, type WidgetDefinition, type WidgetOption, type WidgetRenderProps } from "./types";

export interface VisibilityControlProps extends VisibilityBinding {
  value: unknown;
  options: readonly WidgetOption[];
  label?: string;
  readOnly?: boolean;
  audienceLabel?: string;
}

/** Inline audience label. Only returned choices can invoke the record's verb. */
export function VisibilityControl({ value, options, label, readOnly, audienceLabel, ...binding }: VisibilityControlProps): ReactElement {
  const t = useUiT();
  const preview = useRuntimeViewAs();
  const settle = useActionResultRun();
  const current = canonicalOptionValue(options, value);
  const allowed = new Set(binding.allowedValues.map((value) => canonicalOptionValue(options, value)));
  const disabled = Boolean(readOnly || binding.disabled || preview.viewAs || preview.pending);
  const latest = useLatestRef({ binding, disabled, options, current, allowed });
  const mutation = useMutation({ mutationFn: async (selected: string) => {
    const state = latest.current;
    const option = state.options.find((entry) => entry.value === selected);
    if (state.disabled || !state.allowed.has(selected) || selected === state.current || !option || option.disabled) return;
    await settle(() => state.binding.onSelect(selected));
  } });
  const badge = <Badge tone="neutral" density="compact" shape="pill">
    <Glyph decorative name="eye" />{audienceLabel ?? optionLabel(options, current ?? String(value ?? ""))}
  </Badge>;
  const destinations = options.filter((option) => option.value !== current && allowed.has(option.value) && !option.disabled);
  if (destinations.length === 0) return badge;
  if (destinations.length === 1) {
    const destination = destinations[0]!;
    const visibility = typeof destination.label === "string" ? destination.label : destination.value;
    return <span className="inline-flex items-center gap-2">{badge}<Button type="button" variant="ghost" size="sm"
      disabled={disabled || mutation.isPending}
      onClick={() => { if (!mutation.isPending) mutation.mutate(destination.value); }}>
      {t("visibility.change", { visibility })}
    </Button></span>;
  }
  return <DropdownMenu.Root>
    <DropdownMenu.Trigger render={<Button type="button" variant="ghost" size="sm"
      disabled={disabled || mutation.isPending} aria-label={label ?? t("visibility.label")} />}>
      {badge}
    </DropdownMenu.Trigger>
    <DropdownMenu.Portal><DropdownMenu.Positioner><DropdownMenu.Content>
      <DropdownMenu.Group>
      <DropdownMenu.Label>{label ?? t("visibility.label")}</DropdownMenu.Label>
      {destinations.map((option) => <DropdownMenu.Item key={option.value}
        disabled={disabled || mutation.isPending}
        onClick={() => { if (!mutation.isPending) mutation.mutate(option.value); }}>
        {option.label}
      </DropdownMenu.Item>)}
      </DropdownMenu.Group>
    </DropdownMenu.Content></DropdownMenu.Positioner></DropdownMenu.Portal>
  </DropdownMenu.Root>;
}

function VisibilityWidget(props: WidgetRenderProps): ReactElement {
  const chrome = useRecordChromeContextMaybe();
  const action = props.field?.visibilityAction;
  if (chrome?.record && action) return <RecordVisibilityWidget {...props} />;
  if (!chrome?.record && props.onChange && !props.readOnly) {
    const Edit = selectWidget.edit;
    if (Edit) return <Edit {...props as WidgetRenderProps<string>} />;
  }
  return <Badge tone="neutral" density="compact">{optionLabel(props.field?.options, canonicalOptionValue(props.field?.options, props.value) ?? String(props.value ?? ""))}</Badge>;
}

function RecordVisibilityWidget({ field }: WidgetRenderProps): ReactElement {
  const { record, recordId, actionsBlocked, formReadOnly } = useRecordChromeContext();
  const action = field?.visibilityAction;
  if (!action) throw new Error("A saved visibility field requires its declared action.");
  const [change, state] = useRecordChromeActionOutcome(action);
  const allowedValues = Array.isArray(record?.allowed_visibility)
    ? record.allowed_visibility.filter((value): value is string => typeof value === "string") : [];
  return <VisibilityControl value={record?.visibility} options={action.options ?? field?.options ?? []}
    audienceLabel={action.audienceField && record
      ? String(rowValueAtPath(record, action.audienceField) ?? "") || undefined : undefined}
    readOnly={formReadOnly || actionsBlocked || state.fetching}
    allowedValues={allowedValues}
    onSelect={(value) => {
      const revision = record?.revision;
      if (!recordId || (action.revisionArgument && typeof revision !== "number")) return Promise.resolve(null);
      return change(recordId, { visibility: value,
        ...(action.revisionArgument ? { [action.revisionArgument]: revision } : {}) });
    }} />;
}

export const visibilityWidget = { read: VisibilityWidget, edit: VisibilityWidget, cell: VisibilityWidget } satisfies WidgetDefinition;
