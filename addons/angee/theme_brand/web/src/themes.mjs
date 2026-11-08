import { createThemeCustomizationOptions, defineTheme } from "@angee/ui/theme-runtime";

const defaults = {
  brand: "#5b5bd6",
  accent: "#0d9488",
  neutral: "#6b7280",
  font: "theme",
  radius: "theme",
  density: "theme",
  elevation: "theme",
};

function migrate(value, fromVersion, currentDefaults) {
  if (fromVersion !== 1 || !value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`Brand options version ${fromVersion} cannot be migrated.`);
  }
  const radii = { "0px": "square", "4px": "compact", "6px": "standard", "8px": "soft", "12px": "round" };
  return {
    ...currentDefaults,
    brand: value.brand,
    accent: value.accent,
    radius: radii[value.radius],
  };
}

export const themes = [defineTheme({
  contractVersion: 1, id: "angee.brand", labelKey: "brand.label", descriptionKey: "brand.description", revision: 2,
  tokens: { shared: {}, light: {}, dark: {} },
  options: createThemeCustomizationOptions(defaults, { version: 2, migrate }),
})];
