import { useMemo } from "react";
import type { HostAppearanceSource } from "@angee/app";
import { useAuthoredQuery } from "@angee/refine";
import {
  isThemeCustomizationOptions,
  parseThemeCustomization,
  resolveThemeOptions,
  type ThemeCustomization,
  type ThemeDefinition,
  type ThemeOptionsEnvelope,
} from "@angee/ui/theme";

import { DefaultBrandKitColors } from "./documents.public";
import { BRAND_KIT_MODEL } from "./views/BrandKitForm";

/**
 * The brand kit's mapping (the brandkit README, "The app's colours"): a customisable
 * theme's options with a kit's colours. Undefined for no theme, a theme that takes no
 * palette, no kit, and a kit or build options the theme's own parser refuses.
 */
export function brandKitOptions(
  theme: ThemeDefinition<unknown> | null,
  kit: Readonly<Record<string, unknown>> | null,
  build: ThemeOptionsEnvelope | undefined,
): ThemeOptionsEnvelope | undefined {
  if (!theme || !kit || !isThemeCustomizationOptions(theme.options)) return undefined;
  const { __typename: _typename, ...colors } = kit;
  try {
    let base: ThemeCustomization = theme.options.defaults;
    if (build !== undefined) base = parseThemeCustomization(resolveThemeOptions(theme, build).value);
    const resolved = resolveThemeOptions(theme, { version: theme.options.version, value: { ...base, ...colors } });
    return { version: resolved.version, value: resolved.value };
  } catch {
    return undefined;
  }
}

/**
 * The app's `hostAppearance` source: the default kit's colours on the host theme
 * (`brandKitOptions`). A theme that takes no palette sends no read. Pending only while
 * the first answer is on its way: a failure, a retry or a paused (offline) read releases it.
 */
export const brandKitHostAppearance: HostAppearanceSource = {
  useOptions(theme, build) {
    const dresses = isThemeCustomizationOptions(theme?.options);
    const read = useAuthoredQuery(DefaultBrandKitColors, undefined, {
      enabled: dresses, dataProviderName: "public", models: [BRAND_KIT_MODEL], errorNotification: false,
    });
    const kit = read.data?.default_brand_kit_colors ?? null;
    const options = useMemo(() => brandKitOptions(theme, kit, build), [theme, kit, build]);
    return { options, pending: dresses && read.isPending && read.fetchStatus === "fetching" && read.failureCount === 0 };
  },
};
