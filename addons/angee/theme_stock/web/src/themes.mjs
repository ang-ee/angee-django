import { createThemeCustomizationOptions, defineTheme } from "@angee/ui/theme-runtime";
export const themes = [defineTheme({
  contractVersion: 1, id: "angee.stock", labelKey: "stock.label",
  descriptionKey: "stock.description", revision: 2,
  tokens: { shared: {}, light: {}, dark: {} },
  options: createThemeCustomizationOptions({
    brand: "#5b5bd6",
    accent: "#0d9488",
    neutral: "#6b7280",
    font: "theme",
    radius: "theme",
    density: "theme",
    elevation: "theme",
  }),
})];
