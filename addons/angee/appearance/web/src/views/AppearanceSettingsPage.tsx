import { useEffect, useMemo, type ReactElement } from "react";
import { Alert, Button, ControlBand, ErrorBanner, PageHeader, RadioGroupItem, RadioGroupRoot, SaveDiscardActions, SettingsSection, SettingsShell, ContainerOutlet, dirtyControlBandClassName, textRoleVariants, useAppRuntime, useAppearance, ThemePreviewFrame, useContainer, useLatestRef, useT, useUnsavedChangesNavigationGuard } from "@angee/ui";
import { parseThemeCustomization, resolveThemeOptions, type ColorScheme, type ThemeContribution, type ThemeCustomizationLogo, type ThemeOptionsEnvelope } from "@angee/ui/theme";
import { useAppearanceT } from "../i18n";

const HOST_VALUE = "__host__";

export function AppearanceSettingsPage(): ReactElement {
  const t = useAppearanceT();
  const themeT = useT("themes");
  const runtime = useAppRuntime();
  const appearance = useAppearance();
  const tools = useContainer("appearance.settings#tools");
  const selectedTheme = appearance.currentPreferences.themeId ?? HOST_VALUE;
  const selectedScheme = appearance.currentPreferences.colorScheme ?? HOST_VALUE;
  const OptionsEditor = appearance.theme?.optionsEditor;
  const readOnly = !appearance.editable;
  const { openDraft, discard, saving } = appearance;
  const dirty = useLatestRef(appearance.dirty);
  useUnsavedChangesNavigationGuard({ isDirty: appearance.dirty, isDirtyNow: () => dirty.current, readOnly });
  // Opening is idempotent; commands that close the draft leave the page ready
  // for another edit. Cleanup is separate so command changes never discard it.
  useEffect(() => {
    if (!readOnly && !saving) openDraft();
  }, [openDraft, readOnly, saving]);
  useEffect(() => discard, [discard]);

  return <>
    <ControlBand className={appearance.dirty ? dirtyControlBandClassName : undefined}>
      {!readOnly ? <SaveDiscardActions isDirty={appearance.dirty} pending={saving} onSave={() => { void appearance.save().catch(() => undefined); }} onDiscard={discard} /> : null}
      {/* Reset is the page's one standing control, and the only way out of an unsupported saved preference. */}
      <Button className="ml-auto" variant="ghost" size="sm" loading={saving} onClick={() => void appearance.reset()}>{t("reset")}</Button>
    </ControlBand>
    <SettingsShell maxWidth="1100" gap="8">
    <PageHeader title={t("title")} description={t("description")} />
    {appearance.notice ? <Alert tone={appearance.notice === "theme-unavailable" ? "warning" : "danger"} title={appearance.notice === "theme-unavailable" ? t("unavailable") : appearance.notice === "options-invalid" ? t("invalidOptions") : t("unsupported")} /> : null}
    <ErrorBanner title={t("saveFailed")} description={appearance.error?.message ?? null} />
    <SettingsSection title={t("theme.title")} description={t("theme.description")}>
      <RadioGroupRoot value={selectedTheme} onValueChange={(value) => void appearance.setTheme(value === HOST_VALUE ? undefined : value)} className="grid gap-3 md:grid-cols-2">
        <RadioGroupItem disabled={readOnly || saving} variant="card" value={HOST_VALUE} label={t("theme.followHost")} description={t("theme.followHostDescription")}>
          <ThemeSpecimen theme={appearance.hostTheme} options={appearance.hostOptions} />
        </RadioGroupItem>
        {runtime.themes.map((theme) => <RadioGroupItem disabled={readOnly || saving} key={theme.definition.id} variant="card" value={theme.definition.id} label={themeT(theme.definition.labelKey)} description={themeT(theme.definition.descriptionKey)}>
          <ThemeSpecimen theme={theme} />
        </RadioGroupItem>)}
      </RadioGroupRoot>
    </SettingsSection>
    <SettingsSection title={t("scheme.title")} description={t("scheme.description")}>
      <RadioGroupRoot orientation="horizontal" value={selectedScheme} onValueChange={(value) => void appearance.setColorScheme(value === HOST_VALUE ? undefined : value as "light" | "dark" | "system")}>
        {[HOST_VALUE, "system", "light", "dark"].map((value) => <RadioGroupItem disabled={readOnly || saving} key={value} value={value} label={value === HOST_VALUE ? t("scheme.host") : t(`scheme.${value}`)} />)}
      </RadioGroupRoot>
    </SettingsSection>
    {OptionsEditor ? <SettingsSection title={t("options.title")} description={t("options.description")}>
      <OptionsEditor definition={appearance.theme!.definition} value={appearance.currentPreferences.options} disabled={saving || readOnly} onChange={(options) => { void appearance.setOptions(options); }} />
    </SettingsSection> : null}
    {appearance.theme ? <SettingsSection title={t("preview.title")} description={t("preview.description")}>
      <div className="grid gap-4 lg:grid-cols-2">{(["light", "dark"] as const).map((scheme) => <FullThemePreview key={scheme} theme={appearance.theme!} scheme={scheme} options={appearance.currentPreferences.options} />)}</div>
    </SettingsSection> : null}
    {tools.length && !readOnly ? <SettingsSection title={t("tools.title")}><ContainerOutlet entries={tools} /></SettingsSection> : null}
    </SettingsShell>
  </>;
}

/** One scheme's resolved tokens and logo for a theme under the given options; invalid options fall back to the theme's defaults. */
function useResolvedScheme(theme: ThemeContribution, scheme: ColorScheme, options?: ThemeOptionsEnvelope): { tokens: Record<string, string>; logo: ThemeCustomizationLogo | undefined } {
  return useMemo(() => {
    let resolved;
    try { resolved = resolveThemeOptions(theme.definition, options); }
    catch { resolved = resolveThemeOptions(theme.definition); }
    let logo: ThemeCustomizationLogo | undefined;
    try { logo = parseThemeCustomization(resolved.value).logo; }
    catch { logo = undefined; }
    return { tokens: { ...resolved.tokens.shared, ...resolved.tokens[scheme] }, logo };
  }, [options, scheme, theme]);
}

function FullThemePreview({ theme, scheme, options }: { theme: ThemeContribution; scheme: ColorScheme; options?: ThemeOptionsEnvelope }): ReactElement {
  const t = useAppearanceT();
  const themeT = useT("themes");
  const { tokens, logo } = useResolvedScheme(theme, scheme, options);
  const Preview = theme.preview;
  return <div className="grid gap-2"><div className={textRoleVariants({ role: "meta" })}>{t(`scheme.${scheme}`)}</div><ThemePreviewFrame title={t("preview.frame", { theme: themeT(theme.definition.labelKey), scheme: t(`scheme.${scheme}`) })} themeId={theme.definition.id} colorScheme={scheme} tokens={tokens} logo={logo}>{Preview ? <Preview definition={theme.definition} colorScheme={scheme} options={options} /> : null}</ThemePreviewFrame></div>;
}

function ThemeSpecimen({ theme, options }: { theme: ThemeContribution | null; options?: ThemeOptionsEnvelope }): ReactElement {
  const t = useAppearanceT();
  return <span className="grid grid-cols-2 gap-2">{(["light", "dark"] as const).map((scheme) => theme
    ? <ThemeScheme key={scheme} theme={theme} scheme={scheme} options={options} />
    : <ThemePreviewFrame key={scheme} title={t("preview.hostCard", { scheme: t(`scheme.${scheme}`) })} themeId={null} colorScheme={scheme} variant="card" />)}</span>;
}

function ThemeScheme({ theme, scheme, options }: { theme: ThemeContribution; scheme: ColorScheme; options?: ThemeOptionsEnvelope }): ReactElement {
  const t = useAppearanceT();
  const themeT = useT("themes");
  const { tokens, logo } = useResolvedScheme(theme, scheme, options);
  return <ThemePreviewFrame title={t("preview.card", { theme: themeT(theme.definition.labelKey), scheme: t(`scheme.${scheme}`) })} themeId={theme.definition.id} colorScheme={scheme} tokens={tokens} logo={logo} variant="card" />;
}
