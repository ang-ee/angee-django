import { createNamespaceT } from "@angee/ui";

export const enAppearanceIntegrateMessages: Record<string, string> = {
  "website.label": "Website URL",
  "website.placeholder": "https://example.com",
  "website.analyse": "Analyse",
  "website.failed": "Website analysis failed",
  "website.neutralTint": "Neutral tint: {{color}}",
  "website.fonts": "Fonts: {{fonts}}",
  "target.label": "Apply palette to",
  "target.unavailable": "No installed theme accepts palette customization.",
  "target.activeUnsupported": "The active theme cannot accept an analysed palette. Choose a customizable theme below.",
  "target.apply": "Customize {{theme}}",
};

export const useAppearanceIntegrateT = createNamespaceT("appearanceIntegrate", enAppearanceIntegrateMessages);
