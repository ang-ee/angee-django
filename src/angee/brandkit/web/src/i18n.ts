import { createNamespaceT } from "@angee/ui/i18n";

// Only the keys the page, the kit form and the contrast widget resolve. The
// colour labels and their sections are the theme customisation editor's own
// (`theme.customization.*` in the shared `ui` namespace), read through `useUiT`.
export const enBrandkitMessages: Record<string, string> = {
  "col.name": "Name",
  "col.default": "Default",
  "form.kit": "Kit",
  "form.colorsHint": "Light values. Themes and mail derive their dark values from them.",
  "form.contrast": "Readability in mail",
  "form.contrastHint": "Mail works its light and dark colours out from these, keeping every text readable.",
  "form.fonts": "Fonts",
  "form.fontsHint": "CSS font stacks, each ending in a generic family such as sans-serif.",
  "form.footer": "Mail footer",
  "form.voice": "Voice",
  "form.voiceHint": "How the brand writes, for people and for AI drafts.",
  "field.isDefault": "Default kit",
  "field.logo": "Logo",
  "field.logoDark": "Logo on dark backgrounds",
  "field.fontHeading": "Headings",
  "field.fontBody": "Body text",
  "field.footerOrganization": "Organization",
  "field.footerAddress": "Postal address",
  "field.contrastWarnings": "Warnings",
  "contrast.none": "Light and dark mail show these colours as stated.",
  "contrast.moved": "{role} in {scheme} mail moves far from this colour to stay readable.",
  "contrast.scheme.LIGHT": "light",
  "contrast.scheme.DARK": "dark",
  "contrast.role.CANVAS": "The page background",
  "contrast.role.SURFACE": "The card",
  "contrast.role.TEXT": "Text",
  "contrast.role.MUTED": "Muted text",
  "contrast.role.LINK": "A link",
};

export const useBrandkitT = createNamespaceT("brandkit", enBrandkitMessages);
