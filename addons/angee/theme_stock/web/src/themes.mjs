import { defineTheme } from "@angee/ui/theme-runtime";
export const themes = [defineTheme({
  contractVersion: 1, id: "angee.stock", labelKey: "stock.label",
  descriptionKey: "stock.description", revision: 1,
  tokens: { shared: {}, light: {}, dark: {} },
})];
