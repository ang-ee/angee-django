import * as React from "react";
import {
  RecordIssues,
  textRoleVariants,
  useActiveRecordForm,
  useUiT,
  type RecordIssue,
  type WidgetDefinition,
  type WidgetRenderProps,
} from "@angee/ui";

import { useBrandkitT } from "./i18n";

/** The leaves a form selects for a kit's computed `contrast_warnings`, which this widget renders itself. */
export const CONTRAST_WARNINGS_SELECTION = [
  "contrast_warnings.seed",
  "contrast_warnings.scheme",
  "contrast_warnings.role",
] as const;

/** The server's `MailSeed` names: each is a kit colour field, labelled as the theme customisation editor labels it. */
const SEEDS = {
  BRAND: { field: "brand", label: "theme.customization.brand" },
  NEUTRAL: { field: "neutral", label: "theme.customization.neutral" },
  CANVAS: { field: "canvas", label: "theme.customization.canvas" },
  SURFACE: { field: "surface", label: "theme.customization.surface" },
} as const;

/** The server's `MailScheme` names; `architecture.guardrails.json` declares the same `contrast.scheme.*` family. */
const SCHEMES = ["LIGHT", "DARK"] as const;

/** The `MailRole` names a warning can carry; `architecture.guardrails.json` declares the same `contrast.role.*` family. */
const ROLES = ["CANVAS", "SURFACE", "TEXT", "MUTED", "LINK"] as const;

interface ContrastWarning {
  seed: keyof typeof SEEDS;
  scheme: (typeof SCHEMES)[number];
  role: (typeof ROLES)[number];
}

function isContrastWarning(item: unknown): item is ContrastWarning {
  if (typeof item !== "object" || item === null) return false;
  const { seed, scheme, role } = item as Record<string, unknown>;
  return typeof seed === "string" && Object.hasOwn(SEEDS, seed)
    && (SCHEMES as readonly unknown[]).includes(scheme)
    && (ROLES as readonly unknown[]).includes(role);
}

/**
 * The mail roles a kit's mail palette moved far from their seed colours to keep
 * them readable, one warning each; a warning focuses its seed's colour field. The
 * kit decides them (`BrandKit.contrast_warnings`); the widget only shows the
 * result, and an empty list says mail wears the colours as stated.
 */
function ContrastWarningsRead({ value }: WidgetRenderProps): React.ReactElement {
  const t = useBrandkitT();
  const ui = useUiT();
  const form = useActiveRecordForm();
  const warnings = Array.isArray(value) ? value.filter(isContrastWarning) : [];
  if (warnings.length === 0) return <span className={textRoleVariants({ role: "meta" })}>{t("contrast.none")}</span>;
  const items: RecordIssue[] = warnings.map((warning) => ({
    id: `${warning.seed}.${warning.scheme}.${warning.role}`,
    tone: "warning",
    field: SEEDS[warning.seed].field,
    message: t("contrast.moved", {
      seed: ui(SEEDS[warning.seed].label),
      role: t(`contrast.role.${warning.role}`),
      scheme: t(`contrast.scheme.${warning.scheme}`),
    }),
  }));
  return <RecordIssues items={items} onFocusField={form?.focusField} />;
}

export const contrastWarningsWidget = {
  read: ContrastWarningsRead,
} satisfies WidgetDefinition;
