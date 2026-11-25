import { useMemo } from "react";
import type { HostAppearanceSource } from "@angee/app";
import { useAuthoredQuery } from "@angee/refine";
import {
  isThemeCustomizationOptions,
  resolveThemeOptions,
  type ThemeDefinition,
  type ThemeOptionsEnvelope,
} from "@angee/ui/theme";

import { DefaultBrandKitColors } from "./documents.public";
import { BRAND_KIT_MODEL } from "./views/BrandKitForm";

/**
 * The options a customisable theme wears with a kit's colours: the kit's ten colours
 * over the build's options for that theme, else over the theme's defaults, so the
 * build's font, radius, density, elevation and logo treatment stay. The theme's own
 * parser judges the result. Undefined for no theme, a theme that takes no palette, no
 * kit, and a kit or build options that parser refuses.
 */
export function brandKitOptions(
  theme: ThemeDefinition<unknown> | null,
  kit: Readonly<Record<string, unknown>> | null,
  build: ThemeOptionsEnvelope | undefined,
): ThemeOptionsEnvelope | undefined {
  if (!theme || !kit || !isThemeCustomizationOptions(theme.options)) return undefined;
  const { __typename: _typename, ...colors } = kit;
  try {
    let base: unknown = theme.options.defaults;
    // The kit's colours go over the build's stated options.
    if (build !== undefined) base = resolveThemeOptions(theme, build).value;
    const resolved = resolveThemeOptions(theme, { version: theme.options.version, value: { ...(base as object), ...colors } });
    return { version: resolved.version, value: resolved.value };
  } catch {
    return undefined;
  }
}

/**
 * The app's appearance at run time: the default kit's colours on the theme a person
 * who chose none wears, signed in or not. A theme that takes no palette sends no read.
 * The first paint holds only while the first answer is on its way: a failure, a retry
 * or a paused (offline) read releases it onto the build's options.
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
