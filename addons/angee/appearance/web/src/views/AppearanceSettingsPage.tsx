import { useEffect, useMemo, useState, type CSSProperties, type ReactElement } from "react";
import {
  Alert,
  Button,
  RadioGroupItem,
  RadioGroupRoot,
  SettingsSection,
  SettingsShell,
  SlotOutlet,
  useAppRuntime,
  useAppearance,
  ThemePreviewFrame,
  useSlot,
  useT,
} from "@angee/ui";
import { parseThemeCustomization, resolveThemeOptions, type ColorScheme, type ThemeContribution, type ThemeCustomizationLogo, type ThemeOptionsEnvelope } from "@angee/ui/theme";
import { APPEARANCE_TOOLS_SLOT } from "../index";
import { useAppearanceT } from "../i18n";

const HOST_VALUE = "__host__";

export function AppearanceSettingsPage(): ReactElement {
  const t = useAppearanceT();
  const themeT = useT("themes");
  const runtime = useAppRuntime();
  const appearance = useAppearance();
  const tools = useSlot(APPEARANCE_TOOLS_SLOT);
  const selectedTheme = appearance.preferences.themeId ?? HOST_VALUE;
  const selectedScheme = appearance.preferences.colorScheme ?? HOST_VALUE;
  const OptionsEditor = appearance.theme?.optionsEditor;
  const readOnly = !appearance.editable;
  const [draft, setDraft] = useState<ThemeOptionsEnvelope | undefined>(appearance.preferences.options);
  useEffect(() => setDraft(appearance.preferences.options), [appearance.preferences.options, appearance.preferences.themeId]);

  return <SettingsShell maxWidth="1100" gap="8">
    <header className="grid gap-1"><h1 className="text-22 font-semibold text-fg">{t("title")}</h1><p className="text-13 text-fg-muted">{t("description")}</p></header>
    {appearance.notice ? <Alert tone={appearance.notice === "theme-unavailable" ? "warning" : "danger"} title={appearance.notice === "theme-unavailable" ? t("unavailable") : appearance.notice === "options-invalid" ? t("invalidOptions") : t("unsupported")} /> : null}
    {appearance.error ? <Alert tone="danger" title={t("saveFailed")}>{appearance.error.message}</Alert> : null}
    <SettingsSection title={t("theme.title")} description={t("theme.description")}>
      <RadioGroupRoot value={selectedTheme} onValueChange={(value) => void appearance.setTheme(value === HOST_VALUE ? undefined : value)} className="grid gap-3 md:grid-cols-2">
        <RadioGroupItem disabled={readOnly} variant="card" value={HOST_VALUE} label={t("theme.followHost")} description={t("theme.followHostDescription")}>
          <ThemeSpecimen theme={appearance.hostTheme} options={appearance.hostOptions} />
        </RadioGroupItem>
        {runtime.themes.map((theme) => <RadioGroupItem disabled={readOnly} key={theme.definition.id} variant="card" value={theme.definition.id} label={themeT(theme.definition.labelKey)} description={themeT(theme.definition.descriptionKey)}>
          <ThemeSpecimen theme={theme} />
        </RadioGroupItem>)}
      </RadioGroupRoot>
    </SettingsSection>
    <SettingsSection title={t("scheme.title")} description={t("scheme.description")}>
      <RadioGroupRoot orientation="horizontal" value={selectedScheme} onValueChange={(value) => void appearance.setColorScheme(value === HOST_VALUE ? undefined : value as "light" | "dark" | "system")}>
        {[HOST_VALUE, "system", "light", "dark"].map((value) => <RadioGroupItem disabled={readOnly} key={value} value={value} label={value === HOST_VALUE ? t("scheme.host") : t(`scheme.${value}`)} />)}
      </RadioGroupRoot>
    </SettingsSection>
    {OptionsEditor ? <SettingsSection title={t("options.title")} description={t("options.description")}>
      <OptionsEditor definition={appearance.theme!.definition} value={draft} disabled={appearance.saving || readOnly} onChange={setDraft} />
      <div className="flex gap-2"><Button disabled={!draft || readOnly} loading={appearance.saving} onClick={() => draft && void appearance.setOptions(draft)}>{appearance.saving ? t("saving") : t("options.apply")}</Button><Button variant="ghost" disabled={readOnly} onClick={() => setDraft(appearance.preferences.options)}>{t("options.cancel")}</Button></div>
    </SettingsSection> : null}
    {appearance.theme ? <SettingsSection title={t("preview.title")} description={t("preview.description")}>
      <div className="grid gap-4 lg:grid-cols-2">{(["light", "dark"] as const).map((scheme) => <FullThemePreview key={scheme} theme={appearance.theme!} scheme={scheme} options={draft ?? appearance.preferences.options} />)}</div>
    </SettingsSection> : null}
    {tools.length && !readOnly ? <SettingsSection title={t("tools.title")}><SlotOutlet entries={tools} /></SettingsSection> : null}
    <div><Button variant="secondary" loading={appearance.saving} onClick={() => void appearance.reset()}>{t("reset")}</Button></div>
  </SettingsShell>;
}

function FullThemePreview({ theme, scheme, options }: { theme: ThemeContribution; scheme: ColorScheme; options?: ThemeOptionsEnvelope }): ReactElement {
  const resolved = useMemo(() => {
    try { return resolveThemeOptions(theme.definition, options); }
    catch { return resolveThemeOptions(theme.definition); }
  }, [options, theme]);
  const tokens = { ...resolved.tokens.shared, ...resolved.tokens[scheme] };
  let logo: ThemeCustomizationLogo | undefined;
  try { logo = parseThemeCustomization(resolved.value).logo; }
  catch { logo = undefined; }
  const Preview = theme.preview;
  return <div className="grid gap-2"><div className="text-12 font-medium capitalize text-fg-muted">{scheme}</div><ThemePreviewFrame title={`${theme.definition.id} ${scheme} preview`} themeId={theme.definition.id} colorScheme={scheme} tokens={tokens} logo={logo}>{Preview ? <Preview definition={theme.definition} colorScheme={scheme} options={options} /> : null}</ThemePreviewFrame></div>;
}

function ThemeSpecimen({ theme, options }: { theme: ThemeContribution | null; options?: ThemeOptionsEnvelope }): ReactElement {
  return <span className="grid grid-cols-2 gap-2">{(["light", "dark"] as const).map((scheme) => theme
    ? <ThemeScheme key={scheme} theme={theme} scheme={scheme} options={options} />
    : <ThemePreviewFrame key={scheme} title={`app default ${scheme} card preview`} themeId={null} colorScheme={scheme} variant="card" />)}</span>;
}

function ThemeScheme({ theme, scheme, options }: { theme: ThemeContribution; scheme: ColorScheme; options?: ThemeOptionsEnvelope }): ReactElement {
  const resolved = useMemo(() => {
    try { return resolveThemeOptions(theme.definition, options); }
    catch { return resolveThemeOptions(theme.definition); }
  }, [options, theme]);
  const tokens = { ...resolved.tokens.shared, ...resolved.tokens[scheme] } as CSSProperties;
  let logo: ThemeCustomizationLogo | undefined;
  try { logo = parseThemeCustomization(resolved.value).logo; }
  catch { logo = undefined; }
  return <ThemePreviewFrame title={`${theme.definition.id} ${scheme} card preview`} themeId={theme.definition.id} colorScheme={scheme} tokens={tokens} logo={logo} variant="card" />;
}
