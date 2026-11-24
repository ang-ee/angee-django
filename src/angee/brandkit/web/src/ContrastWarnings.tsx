import * as React from "react";
import { Alert, textRoleVariants, useUiT, type WidgetDefinition, type WidgetRenderProps } from "@angee/ui";

import { useBrandkitT } from "./i18n";

/** The leaves a form selects for a kit's computed `contrast_warnings`, which this widget renders itself. */
export const CONTRAST_WARNINGS_SELECTION = [
  "contrast_warnings.seed",
  "contrast_warnings.scheme",
  "contrast_warnings.role",
] as const;

interface ContrastWarning {
  /** The kit's colour field, which is also its theme customisation input. */
  seed: string;
  scheme: string;
  role: string;
}

function isContrastWarning(item: unknown): item is ContrastWarning {
  if (typeof item !== "object" || item === null) return false;
  const { seed, scheme, role } = item as Record<string, unknown>;
  return typeof seed === "string" && typeof scheme === "string" && typeof role === "string";
}

/**
 * The mail roles a kit's mail palette moved far from their seed colours to keep
 * them readable, one warning each, titled with the seed's theme customisation
 * label. The kit decides them (`BrandKit.contrast_warnings`); the widget only
 * shows the result, and an empty list says mail wears the colours as stated.
 */
function ContrastWarningsRead({ value }: WidgetRenderProps): React.ReactElement {
  const t = useBrandkitT();
  const ui = useUiT();
  const warnings = Array.isArray(value) ? value.filter(isContrastWarning) : [];
  if (warnings.length === 0) return <span className={textRoleVariants({ role: "meta" })}>{t("contrast.none")}</span>;
  return (
    <div className="grid gap-2">
      {warnings.map((warning) => (
        <Alert
          key={`${warning.seed}.${warning.scheme}.${warning.role}`}
          tone="warning"
          title={ui(`theme.customization.${warning.seed}`)}
        >
          {t("contrast.moved", {
            role: t(`contrast.role.${warning.role}`),
            scheme: t(`contrast.scheme.${warning.scheme}`),
          })}
        </Alert>
      ))}
    </div>
  );
}

export const contrastWarningsWidget = {
  read: ContrastWarningsRead,
} satisfies WidgetDefinition;
