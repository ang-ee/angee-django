import { useMemo, useState, type ReactElement } from "react";
import { Alert, Button, Chip, ErrorBanner, FieldDescription, FieldLabel, FieldRoot, Input, MetaGrid, Select, errorMessage, useAppRuntime, useAppearance, useT } from "@angee/ui";
import { isThemeCustomizationOptions, type ThemeContribution } from "@angee/ui/theme";
import { useAuthoredQuery } from "@angee/refine";
import { AnalyseAppearance } from "./documents";
import { useAppearanceIntegrateT } from "./i18n";

export function WebsiteAppearanceTool(): ReactElement {
  const appearance = useAppearance();
  const runtime = useAppRuntime();
  const themeT = useT("themes");
  const t = useAppearanceIntegrateT();
  const [url, setUrl] = useState("");
  const [submitted, setSubmitted] = useState("");
  const compatibleThemes = useMemo(
    () => runtime.themes.filter((theme): theme is ThemeContribution => isThemeCustomizationOptions(theme.definition.options)),
    [runtime.themes],
  );
  const activeThemeId = appearance.theme && isThemeCustomizationOptions(appearance.theme.definition.options)
    ? appearance.theme.definition.id
    : "";
  // The target follows the active theme until the user picks one. A pick that
  // is no longer installed falls back to that default.
  const [chosenThemeId, setChosenThemeId] = useState<string | null>(null);
  const targetThemeId = compatibleThemes.some((theme) => theme.definition.id === chosenThemeId)
    ? chosenThemeId!
    : activeThemeId || compatibleThemes[0]?.definition.id || "";
  const query = useAuthoredQuery(AnalyseAppearance, { url: submitted }, { enabled: Boolean(submitted) });
  const result = query.data?.analyseAppearance;
  const targetTheme = compatibleThemes.find((theme) => theme.definition.id === targetThemeId);
  async function analyse(): Promise<void> {
    if (!url.trim()) return;
    if (submitted === url.trim()) await query.refetch();
    else setSubmitted(url.trim());
  }
  async function applyPalette(): Promise<void> {
    const brand = result?.colors[0];
    const definition = targetTheme?.definition;
    if (!brand || !definition || !isThemeCustomizationOptions(definition.options)) return;
    const accent = result.colors[1] ?? brand;
    await appearance.setTheme(definition.id, {
      version: definition.options.version,
      value: {
        ...definition.options.defaults,
        brand,
        accent,
        neutral: result.neutralTint || definition.options.defaults.neutral,
      },
    });
  }
  return <div className="grid gap-4">
    <FieldRoot><FieldLabel htmlFor="appearance-website">{t("website.label")}</FieldLabel><div className="flex flex-col gap-2 sm:flex-row"><Input className="min-w-0 flex-1" id="appearance-website" type="url" value={url} placeholder={t("website.placeholder")} onChange={(event) => setUrl(event.target.value)} /><Button loading={query.isFetching} onClick={() => void analyse()}>{t("website.analyse")}</Button></div></FieldRoot>
    <ErrorBanner title={t("website.failed")} description={query.error ? errorMessage(query.error, t("website.failed")) : null} />
    {compatibleThemes.length ? <FieldRoot><FieldLabel htmlFor="appearance-target-theme">{t("target.label")}</FieldLabel><Select id="appearance-target-theme" value={targetThemeId} onValueChange={setChosenThemeId} options={compatibleThemes.map((theme) => ({ value: theme.definition.id, label: themeT(theme.definition.labelKey) }))} />{!activeThemeId ? <FieldDescription>{t("target.activeUnsupported")}</FieldDescription> : null}</FieldRoot> : <Alert tone="warning" title={t("target.unavailable")} />}
    {result ? <div className="grid gap-3">
      <MetaGrid rows={[
        [t("website.site"), result.siteName || result.title || result.finalUrl],
        [t("website.url"), result.finalUrl],
        [t("website.colors"), <span className="flex flex-wrap gap-1">{result.colors.map((color) => <Chip key={color} color={color} size="sm" mono>{color}</Chip>)}</span>],
        ...(result.neutralTint ? [[t("website.neutralTint"), <Chip color={result.neutralTint} size="sm" mono>{result.neutralTint}</Chip>] as const] : []),
        ...(result.fonts.length ? [[t("website.fonts"), result.fonts.join(", ")] as const] : []),
      ]} />
      {result.warnings.map((warning) => <Alert key={warning} tone="warning" title={warning} />)}
      <div><Button disabled={!result.colors.length || !targetTheme} onClick={() => void applyPalette()}>{t("target.apply", { theme: targetTheme ? themeT(targetTheme.definition.labelKey) : "" })}</Button></div>
    </div> : null}
  </div>;
}
