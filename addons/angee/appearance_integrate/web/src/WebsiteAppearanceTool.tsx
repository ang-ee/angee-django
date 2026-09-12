import { useEffect, useMemo, useState, type ReactElement } from "react";
import { Alert, Button, Card, CardContent, Input, Label, Select, errorMessage, useAppRuntime, useAppearance, useT } from "@angee/ui";
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
  const [targetThemeId, setTargetThemeId] = useState(activeThemeId || compatibleThemes[0]?.definition.id || "");
  useEffect(() => {
    if (activeThemeId) setTargetThemeId(activeThemeId);
    else if (!compatibleThemes.some((theme) => theme.definition.id === targetThemeId)) {
      setTargetThemeId(compatibleThemes[0]?.definition.id ?? "");
    }
  }, [activeThemeId, compatibleThemes, targetThemeId]);
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
        neutral: result.neutralTint ?? definition.options.defaults.neutral,
      },
    });
  }
  return <Card><CardContent className="grid gap-4">
    <div className="grid gap-1"><Label htmlFor="appearance-website">{t("website.label")}</Label><div className="flex flex-col gap-2 sm:flex-row"><Input className="min-w-0 flex-1" id="appearance-website" type="url" value={url} placeholder={t("website.placeholder")} onChange={(event) => setUrl(event.target.value)} /><Button loading={query.isFetching} onClick={() => void analyse()}>{t("website.analyse")}</Button></div></div>
    {query.error ? <Alert tone="danger" title={t("website.failed")}>{errorMessage(query.error)}</Alert> : null}
    {compatibleThemes.length ? <div className="grid gap-1"><Label htmlFor="appearance-target-theme">{t("target.label")}</Label>{!activeThemeId ? <p className="text-12 text-fg-muted">{t("target.activeUnsupported")}</p> : null}<Select id="appearance-target-theme" value={targetThemeId} onValueChange={setTargetThemeId} options={compatibleThemes.map((theme) => ({ value: theme.definition.id, label: themeT(theme.definition.labelKey) }))} /></div> : <Alert tone="warning" title={t("target.unavailable")} />}
    {result ? <div className="grid gap-3"><div><strong>{result.siteName || result.title || result.finalUrl}</strong><div className="text-xs text-fg-muted">{result.finalUrl}</div></div><div className="flex flex-wrap gap-2">{result.colors.map((color) => <span key={color} className="inline-flex items-center gap-1 text-xs"><span className="size-5 rounded-full border border-border" style={{ backgroundColor: color }} />{color}</span>)}</div>{result.neutralTint ? <div className="text-xs text-fg-muted">{t("website.neutralTint", { color: result.neutralTint })}</div> : null}{result.fonts.length ? <div className="text-xs text-fg-muted">{t("website.fonts", { fonts: result.fonts.join(", ") })}</div> : null}{result.warnings.map((warning) => <Alert key={warning} tone="warning" title={warning} />)}<Button disabled={!result.colors.length || !targetTheme} onClick={() => void applyPalette()}>{t("target.apply", { theme: targetTheme ? themeT(targetTheme.definition.labelKey) : "" })}</Button></div> : null}
  </CardContent></Card>;
}
