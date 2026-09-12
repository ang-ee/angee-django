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
import { ThemeLogo } from "./logo";

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
const LOGO_OPTIONS = [
  ["theme", "theme.customization.themeDefault"],
  ["brand", "theme.customization.logo.brand"],
  ["accent", "theme.customization.logo.accent"],
  ["mono", "theme.customization.logo.mono"],
  ["star", "theme.customization.logo.star"],
  ["corner", "theme.customization.logo.corner"],
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
    <CustomizationSection title={t("theme.customization.section.identity")}>
      <div className="grid gap-4 sm:grid-cols-3">
        <ThemeColorField disabled={disabled} label={t("theme.customization.brand")} value={customization.brand} onChange={(brand) => update({ brand })} />
        <ThemeColorField disabled={disabled} label={t("theme.customization.accent")} value={customization.accent} onChange={(accent) => update({ accent })} />
        <ThemeColorField disabled={disabled} label={t("theme.customization.neutral")} value={customization.neutral} onChange={(neutral) => update({ neutral })} />
      </div>
    </CustomizationSection>
    <CustomizationSection title={t("theme.customization.section.surfaces")}>
      <div className="grid gap-4 sm:grid-cols-3">
        <ThemeColorField disabled={disabled} label={t("theme.customization.canvas")} value={customization.canvas} onChange={(canvas) => update({ canvas })} />
        <ThemeColorField disabled={disabled} label={t("theme.customization.surface")} value={customization.surface} onChange={(surface) => update({ surface })} />
        <ThemeColorField disabled={disabled} label={t("theme.customization.rail")} value={customization.rail} onChange={(rail) => update({ rail })} />
      </div>
    </CustomizationSection>
    <CustomizationSection title={t("theme.customization.section.feedback")}>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <ThemeColorField disabled={disabled} label={t("theme.customization.success")} value={customization.success} onChange={(success) => update({ success })} />
        <ThemeColorField disabled={disabled} label={t("theme.customization.warning")} value={customization.warning} onChange={(warning) => update({ warning })} />
        <ThemeColorField disabled={disabled} label={t("theme.customization.danger")} value={customization.danger} onChange={(danger) => update({ danger })} />
        <ThemeColorField disabled={disabled} label={t("theme.customization.info")} value={customization.info} onChange={(info) => update({ info })} />
      </div>
    </CustomizationSection>
    <CustomizationSection title={t("theme.customization.section.form")}>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <ThemeSelect disabled={disabled} label={t("theme.customization.font")} value={customization.font} choices={FONT_OPTIONS} onChange={(font) => update({ font: font as ThemeCustomization["font"] })} />
        <ThemeSelect disabled={disabled} label={t("theme.customization.radius")} value={customization.radius} choices={RADIUS_OPTIONS} onChange={(radius) => update({ radius: radius as ThemeCustomization["radius"] })} />
        <ThemeSelect disabled={disabled} label={t("theme.customization.density")} value={customization.density} choices={DENSITY_OPTIONS} onChange={(density) => update({ density: density as ThemeCustomization["density"] })} />
        <ThemeSelect disabled={disabled} label={t("theme.customization.elevation")} value={customization.elevation} choices={ELEVATION_OPTIONS} onChange={(elevation) => update({ elevation: elevation as ThemeCustomization["elevation"] })} />
        <div className="grid grid-cols-[minmax(0,1fr)_2.25rem] items-end gap-2">
          <ThemeSelect disabled={disabled} label={t("theme.customization.logo")} value={customization.logo} choices={LOGO_OPTIONS} onChange={(logo) => update({ logo: logo as ThemeCustomization["logo"] })} />
          <span className="grid size-9 place-content-center rounded-6 border border-border bg-rail text-on-rail">
            <ThemeLogo logo={customization.logo} size={22} width={22} height={22} />
          </span>
        </div>
      </div>
    </CustomizationSection>
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

function CustomizationSection({
  children,
  title,
}: {
  children: ReactElement;
  title: string;
}): ReactElement {
  return <section className="grid gap-3">
    <h3 className="text-12 font-semibold uppercase tracking-wide text-fg-muted">{title}</h3>
    {children}
  </section>;
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
    && left.canvas === right.canvas
    && left.surface === right.surface
    && left.rail === right.rail
    && left.success === right.success
    && left.warning === right.warning
    && left.danger === right.danger
    && left.info === right.info
    && left.font === right.font
    && left.radius === right.radius
    && left.density === right.density
    && left.elevation === right.elevation
    && left.logo === right.logo;
}
