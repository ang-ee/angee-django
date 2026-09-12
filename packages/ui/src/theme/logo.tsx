import { AngeeLogo } from "@angee/logo-react";
import type { ComponentProps, ReactElement } from "react";

import { useOptionalAppearance } from "./appearance";
import type { ThemeCustomizationLogo } from "./runtime.mjs";

type AngeeLogoProps = ComponentProps<typeof AngeeLogo>;

export interface ThemeLogoProps extends Omit<
  AngeeLogoProps,
  "bgColor" | "colors" | "geometry" | "preset" | "rotation" | "scheme"
> {
  /** Override the active appearance choice for previews and authored placements. */
  logo?: ThemeCustomizationLogo;
  /** Preserve an authored geometry when the theme-default logo is selected. */
  themeGeometry?: AngeeLogoProps["geometry"];
}

/** The application mark resolved from the active theme customization. */
export function ThemeLogo({ logo, themeGeometry, ...props }: ThemeLogoProps): ReactElement {
  const resolvedLogo = useThemeLogoChoice(logo);
  if (resolvedLogo === "theme") {
    return <AngeeLogo {...props} preset="gold" geometry={themeGeometry} bgColor={null} />;
  }

  const palette = resolvedLogo === "accent"
    ? {
        top: "var(--accent)",
        right: "var(--accent-soft-text)",
        left: "var(--brand)",
      }
    : {
        top: "var(--brand)",
        right: "var(--brand-hover)",
        left: "var(--brand-active)",
      };
  const geometry = resolvedLogo === "corner" ? "tripod1" : "full";
  const rotation = resolvedLogo === "star" ? "star" : resolvedLogo === "corner" ? "iso" : "rotated";
  const colors = resolvedLogo === "mono"
    ? { top: "var(--brand)", right: "var(--brand)", left: "var(--brand)" }
    : palette;

  return <AngeeLogo
    {...props}
    geometry={geometry}
    rotation={rotation}
    scheme={resolvedLogo === "mono" ? "mono" : "3tone"}
    colors={colors}
    bgColor={null}
    stroke="transparent"
  />;
}

/** Resolve the active bounded logo treatment for brand placements with custom layout. */
export function useThemeLogoChoice(override?: ThemeCustomizationLogo): ThemeCustomizationLogo {
  const appearance = useOptionalAppearance();
  return override ?? readLogoChoice(appearance?.effectiveOptions?.value);
}

function readLogoChoice(value: unknown): ThemeCustomizationLogo {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "theme";
  const logo = (value as { logo?: unknown }).logo;
  return logo === "brand" || logo === "accent" || logo === "mono"
    || logo === "star" || logo === "corner"
    ? logo
    : "theme";
}
