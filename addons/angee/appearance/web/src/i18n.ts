import { createNamespaceT } from "@angee/ui";
export const enAppearanceMessages: Record<string, string> = {
  "title": "Appearance", "description": "Choose how Angee looks for your account.",
  "theme.title": "Theme", "theme.description": "Installed theme addons available in this app.",
  "theme.followHost": "Follow app default", "scheme.title": "Color scheme",
  "scheme.description": "Use a light or dark scheme, or follow your device.",
  "scheme.host": "Follow app default", "scheme.system": "System", "scheme.light": "Light", "scheme.dark": "Dark",
  "options.title": "Customize theme", "options.description": "Start with the selected theme, preview your changes in both schemes, then apply them to your account.", "options.apply": "Apply customization", "options.cancel": "Cancel changes",
  "preview.title": "Full preview", "preview.description": "Review shared controls and data surfaces in isolated light and dark documents.",
  "reset": "Reset appearance", "saving": "Saving…", "unavailable": "Your selected theme is not currently installed. The app default is shown while the saved choice is retained.",
  "invalidOptions": "The saved options cannot be used by this theme. Its defaults are shown until you apply new options.",
  "unsupported": "This appearance preference was written by a newer version. Reset it to edit here.",
  "saveFailed": "Appearance could not be saved.", "tools.title": "Appearance tools",
};
export const useAppearanceT = createNamespaceT("appearance", enAppearanceMessages);
