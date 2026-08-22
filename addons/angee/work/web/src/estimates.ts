import type { WorkT } from "./i18n";

export type EstimateScale =
  | "NONE"
  | "LINEAR"
  | "FIBONACCI"
  | "EXPONENTIAL"
  | "TSHIRT";

/** Render a stored estimate through the queue's declared estimation vocabulary. */
export function estimateLabel(
  value: unknown,
  scale: unknown,
  t: WorkT,
): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  const normalized = String(scale ?? "").trim().toUpperCase() as EstimateScale;
  if (normalized === "NONE") return null;
  if (normalized !== "TSHIRT") return t("estimate.points", { count: value });
  switch (value) {
    case 1:
      return t("estimate.size.xs");
    case 2:
      return t("estimate.size.s");
    case 3:
      return t("estimate.size.m");
    case 5:
      return t("estimate.size.l");
    case 8:
      return t("estimate.size.xl");
    case 13:
      return t("estimate.size.xxl");
    default:
      return t("estimate.size.unknown", { value });
  }
}
