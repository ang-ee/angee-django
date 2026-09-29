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
import { canonicalOptionValue, optionLabel, type VisibilityBinding, type WidgetDefinition, type WidgetOption, type WidgetRenderProps } from "./types";

export interface VisibilityControlProps extends VisibilityBinding {
  value: unknown;
  options: readonly WidgetOption[];
  label?: string;
  readOnly?: boolean;
}

/** Inline audience label. Only returned choices can invoke the record's verb. */
export function VisibilityControl({ value, options, label, readOnly, ...binding }: VisibilityControlProps): ReactElement {
  const t = useUiT();
  const preview = useRuntimeViewAs();
  const settle = useActionResultRun();
  const current = canonicalOptionValue(options, value);
  const currentIndex = options.findIndex((option) => option.value === current);
  const allowed = new Set(binding.allowedValues.map((value) => canonicalOptionValue(options, value)));
  const isLocked = (option: WidgetOption, index: number) => option.disabled ||
    (binding.monotone && currentIndex >= 0 && index < currentIndex);
  const choices = options.filter((option, index) => allowed.has(option.value) ||
    (binding.monotone && currentIndex >= 0 && index < currentIndex));
  const disabled = Boolean(readOnly || binding.disabled || preview.viewAs || preview.pending);
  const latest = useLatestRef({ binding, disabled, options, current, currentIndex, allowed });
  const mutation = useMutation({ mutationFn: async (selected: string) => {
    const state = latest.current;
    const index = state.options.findIndex((option) => option.value === selected);
    if (state.disabled || !state.allowed.has(selected) || selected === state.current || index < 0 ||
        state.options[index]?.disabled || (state.binding.monotone && index < state.currentIndex)) return;
    await settle(() => state.binding.onSelect(selected));
  } });
  const badge = <Badge tone="neutral" density="compact" shape="pill">
    <Glyph decorative name="eye" />{optionLabel(options, current ?? String(value ?? ""))}
  </Badge>;
  const destinations = choices.filter((option) => option.value !== current && !isLocked(option, options.indexOf(option)));
  const onlyDestination = destinations.length === 1 ? destinations[0] : undefined;
  if (onlyDestination && !binding.monotone) {
    const audience = typeof onlyDestination.label === "string" ? onlyDestination.label : onlyDestination.value;
    return <Button type="button" variant="ghost" size="sm" disabled={disabled || mutation.isPending}
      aria-label={t("visibility.change", { audience })}
      onClick={() => { if (!mutation.isPending) mutation.mutate(onlyDestination.value); }}>
      {badge}<Glyph decorative name="arrow-right" />{onlyDestination.label}
    </Button>;
  }
  if (!choices.some((option) => option.value !== current)) return badge;
  return <DropdownMenu.Root>
    <DropdownMenu.Trigger render={<Button type="button" variant="ghost" size="sm"
      disabled={disabled || mutation.isPending} aria-label={label ?? t("visibility.label")} />}>
      {badge}
    </DropdownMenu.Trigger>
    <DropdownMenu.Portal><DropdownMenu.Positioner><DropdownMenu.Content>
      <DropdownMenu.Label>{label ?? t("visibility.label")}</DropdownMenu.Label>
      {choices.map((option) => <DropdownMenu.Item key={option.value}
        disabled={disabled || mutation.isPending || option.value === current || Boolean(isLocked(option, options.indexOf(option)))}
        onClick={() => { if (!mutation.isPending) mutation.mutate(option.value); }}>
        {option.label}{isLocked(option, options.indexOf(option)) ? <Glyph decorative name="lock" /> : null}
      </DropdownMenu.Item>)}
    </DropdownMenu.Content></DropdownMenu.Positioner></DropdownMenu.Portal>
  </DropdownMenu.Root>;
}

function VisibilityWidget({ value, field, readOnly }: WidgetRenderProps): ReactElement {
  const binding = field?.visibility;
  if (!binding) return <Badge tone="neutral" density="compact">{optionLabel(field?.options, canonicalOptionValue(field?.options, value) ?? String(value ?? ""))}</Badge>;
  return <VisibilityControl {...binding} value={value} options={field?.options ?? []} readOnly={readOnly} />;
}

export const visibilityWidget = { read: VisibilityWidget, edit: VisibilityWidget, cell: VisibilityWidget } satisfies WidgetDefinition;
