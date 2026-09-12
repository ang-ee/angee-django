import { useId, type ReactElement } from "react";

import { useUiT } from "../i18n";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Label } from "../ui/label";
import { Select } from "../ui/select";
import type { ThemeOptionsEditorProps } from "./index";
import {
  parseThemeCustomization,
  resolveThemeOptions,
  type ThemeCustomization,
} from "./runtime.mjs";

const FONT_OPTIONS = [
  ["theme", "theme.customization.themeDefault"],
  ["system", "theme.customization.font.system"],
  ["inter", "theme.customization.font.inter"],
  ["humanist", "theme.customization.font.humanist"],
  ["industrial", "theme.customization.font.industrial"],
  ["editorial", "theme.customization.font.editorial"],
  ["mono", "theme.customization.font.mono"],
] as const;
const RADIUS_OPTIONS = [
  ["theme", "theme.customization.themeDefault"],
  ["square", "theme.customization.radius.square"],
  ["compact", "theme.customization.radius.compact"],
  ["standard", "theme.customization.radius.standard"],
  ["soft", "theme.customization.radius.soft"],
  ["round", "theme.customization.radius.round"],
] as const;
const DENSITY_OPTIONS = [
  ["theme", "theme.customization.themeDefault"],
  ["compact", "theme.customization.density.compact"],
  ["balanced", "theme.customization.density.balanced"],
  ["comfortable", "theme.customization.density.comfortable"],
  ["spacious", "theme.customization.density.spacious"],
] as const;
const ELEVATION_OPTIONS = [
  ["theme", "theme.customization.themeDefault"],
  ["flat", "theme.customization.elevation.flat"],
  ["subtle", "theme.customization.elevation.subtle"],
  ["soft", "theme.customization.elevation.soft"],
  ["dramatic", "theme.customization.elevation.dramatic"],
] as const;

/** Shared editor for themes using createThemeCustomizationOptions. */
export function ThemeCustomizationEditor({
  definition,
  value,
  disabled,
  onChange,
}: ThemeOptionsEditorProps): ReactElement {
  const t = useUiT();
  const options = definition.options;
  if (!options) throw new Error(`Theme ${definition.id} does not declare customization options.`);

  let customization: ThemeCustomization;
  try {
    customization = parseThemeCustomization(resolveThemeOptions(definition, value).value);
  } catch {
    customization = parseThemeCustomization(options.defaults);
  }
  const defaults = parseThemeCustomization(options.defaults);
  const update = (patch: Partial<ThemeCustomization>) => {
    onChange({ version: options.version, value: { ...customization, ...patch } });
  };

  return <div className="grid gap-5">
    <p className="text-13 text-fg-muted">{t("theme.customization.description")}</p>
    <div className="grid gap-4 sm:grid-cols-3">
      <ThemeColorField disabled={disabled} label={t("theme.customization.brand")} value={customization.brand} onChange={(brand) => update({ brand })} />
      <ThemeColorField disabled={disabled} label={t("theme.customization.accent")} value={customization.accent} onChange={(accent) => update({ accent })} />
      <ThemeColorField disabled={disabled} label={t("theme.customization.neutral")} value={customization.neutral} onChange={(neutral) => update({ neutral })} />
    </div>
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <ThemeSelect disabled={disabled} label={t("theme.customization.font")} value={customization.font} choices={FONT_OPTIONS} onChange={(font) => update({ font: font as ThemeCustomization["font"] })} />
      <ThemeSelect disabled={disabled} label={t("theme.customization.radius")} value={customization.radius} choices={RADIUS_OPTIONS} onChange={(radius) => update({ radius: radius as ThemeCustomization["radius"] })} />
      <ThemeSelect disabled={disabled} label={t("theme.customization.density")} value={customization.density} choices={DENSITY_OPTIONS} onChange={(density) => update({ density: density as ThemeCustomization["density"] })} />
      <ThemeSelect disabled={disabled} label={t("theme.customization.elevation")} value={customization.elevation} choices={ELEVATION_OPTIONS} onChange={(elevation) => update({ elevation: elevation as ThemeCustomization["elevation"] })} />
    </div>
    <div>
      <Button
        type="button"
        variant="ghost"
        disabled={disabled || sameCustomization(customization, defaults)}
        onClick={() => onChange({ version: options.version, value: defaults })}
      >
        {t("theme.customization.restore")}
      </Button>
    </div>
  </div>;
}

function ThemeColorField({
  disabled,
  label,
  value,
  onChange,
}: {
  disabled?: boolean;
  label: string;
  value: string;
  onChange: (value: string) => void;
}): ReactElement {
  const id = useId();
  return <div className="grid gap-1.5">
    <Label htmlFor={id}>{label}</Label>
    <div className="flex items-center gap-2 rounded-6 border border-border bg-sheet p-1">
      <Input
        id={id}
        disabled={disabled}
        type="color"
        value={value}
        className="h-8 w-12 shrink-0 cursor-pointer border-0 p-0 disabled:cursor-not-allowed"
        onChange={(event) => onChange(event.currentTarget.value.toLowerCase())}
      />
      <code className="text-12 uppercase text-fg-muted">{value}</code>
    </div>
  </div>;
}

function ThemeSelect({
  choices,
  disabled,
  label,
  value,
  onChange,
}: {
  choices: readonly (readonly [string, string])[];
  disabled?: boolean;
  label: string;
  value: string;
  onChange: (value: string) => void;
}): ReactElement {
  const t = useUiT();
  const id = useId();
  return <div className="grid gap-1.5">
    <Label htmlFor={id}>{label}</Label>
    <Select
      id={id}
      disabled={disabled}
      value={value}
      onValueChange={onChange}
      options={choices.map(([choice, labelKey]) => ({ value: choice, label: t(labelKey) }))}
    />
  </div>;
}

function sameCustomization(left: ThemeCustomization, right: ThemeCustomization): boolean {
  return left.brand === right.brand
    && left.accent === right.accent
    && left.neutral === right.neutral
    && left.font === right.font
    && left.radius === right.radius
    && left.density === right.density
    && left.elevation === right.elevation;
}
