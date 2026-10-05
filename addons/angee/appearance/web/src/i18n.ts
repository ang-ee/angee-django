import { createNamespaceT } from "@angee/ui";
export const enAppearanceMessages: Record<string, string> = {
  "title": "Appearance", "description": "Choose how Angee looks for your account.",
  "theme.title": "Theme", "theme.description": "Installed theme addons available in this app.",
  "theme.followHost": "Follow app default", "theme.followHostDescription": "Use the theme chosen by this app.", "scheme.title": "Color scheme",
  "scheme.description": "Use a light or dark scheme, or follow your device.",
  "scheme.host": "Follow app default", "scheme.system": "System", "scheme.light": "Light", "scheme.dark": "Dark",
  "options.title": "Customize theme", "options.description": "Preview changes throughout the app and in both schemes, then Save in the control band to keep them.",
  "preview.title": "Full preview", "preview.description": "Review shared controls and data surfaces in isolated light and dark documents.",
  "reset": "Reset appearance", "unavailable": "Your selected theme is not currently installed. The app default is shown while the saved choice is retained.",
  "invalidOptions": "The saved options cannot be used by this theme. Its defaults are shown until you edit and save new options.",
  "unsupported": "This appearance preference was written by a newer version. Reset it to edit here.",
  "saveFailed": "Appearance could not be saved.", "tools.title": "Appearance tools",
};
export const useAppearanceT = createNamespaceT("appearance", enAppearanceMessages);
