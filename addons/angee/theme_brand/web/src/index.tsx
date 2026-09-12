import { defineBaseAddon } from "@angee/app";
import { defineThemeContribution, type ThemeOptionsEditorProps } from "@angee/ui/theme";
import { Input, Label, Select } from "@angee/ui";
import { themes } from "./themes.mjs";

function BrandOptionsEditor({ value, disabled, onChange }: ThemeOptionsEditorProps) {
  const parsed = themes[0].options!.parse(value?.value ?? themes[0].options!.defaults);
  const update = (patch: Partial<typeof parsed>) => onChange({ version: 1, value: { ...parsed, ...patch } });
  return <div className="grid gap-4 sm:grid-cols-3">
    <Label>Brand color<Input disabled={disabled} type="color" value={parsed.brand} onChange={(event) => update({ brand: event.target.value })} /></Label>
    <Label>Accent color<Input disabled={disabled} type="color" value={parsed.accent} onChange={(event) => update({ accent: event.target.value })} /></Label>
    <Label>Corner radius<Select disabled={disabled} value={parsed.radius} onValueChange={(radius) => update({ radius })} options={[{ label: "Square", value: "0px" }, { label: "Compact", value: "4px" }, { label: "Standard", value: "6px" }, { label: "Soft", value: "8px" }, { label: "Round", value: "12px" }]} /></Label>
  </div>;
}

export default defineBaseAddon({ id: "theme.brand", themes: [defineThemeContribution({ definition: themes[0], optionsEditor: BrandOptionsEditor })], i18n: { themes: { "brand.label": "Brand", "brand.description": "Choose bounded brand colors and control curvature." } } });
