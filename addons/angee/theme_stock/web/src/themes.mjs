import { createThemeCustomizationOptions, defineTheme, migrateThemeCustomization } from "@angee/ui/theme-runtime";

function migrate(value, fromVersion, defaults) {
  if (fromVersion === 2) return migrateThemeCustomization(value, defaults);
  if (fromVersion !== 1 || !value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`Default theme options version ${fromVersion} cannot be migrated.`);
  }
  const radii = { "0px": "square", "4px": "compact", "6px": "standard", "8px": "soft", "12px": "round" };
  return migrateThemeCustomization({
    ...value,
    ...(typeof value.radius === "string" && value.radius in radii ? { radius: radii[value.radius] } : {}),
  }, defaults);
}

export const themes = [defineTheme({
  contractVersion: 1, id: "angee.stock", labelKey: "stock.label",
  legacyIds: ["angee.brand"],
  descriptionKey: "stock.description", revision: 4,
  tokens: { shared: {}, light: {}, dark: {} },
  options: createThemeCustomizationOptions({
    brand: "#5b5bd6",
    accent: "#0d9488",
    neutral: "#6b7280",
    canvas: "#f7f8fa",
    surface: "#ffffff",
    rail: "#0a0c10",
    success: "#10b981",
    warning: "#f59e0b",
    danger: "#ef4444",
    info: "#3b82f6",
    font: "theme",
    radius: "theme",
    density: "theme",
    elevation: "theme",
    logo: "theme",
  }, { version: 3, migrate }),
})];
