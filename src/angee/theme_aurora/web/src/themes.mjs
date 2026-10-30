import { defineTheme } from "@angee/ui/theme-runtime";
export const themes = [defineTheme({
  contractVersion: 1, id: "angee.aurora", labelKey: "aurora.label", descriptionKey: "aurora.description", revision: 1, stylesheets: ["./theme.css"],
  tokens: {
    shared: { "--r-2": "4px", "--r-4": "8px", "--r-6": "10px", "--r-8": "14px", "--r-10": "18px", "--r-12": "22px", "--font-family-sans": "Avenir Next, Inter, sans-serif", "--control-h-sm": "28px", "--control-h-md": "34px", "--control-h-lg": "40px" },
    light: { "--surface-canvas": "#f3fbf8", "--surface-sheet": "#ffffff", "--surface-sheet-2": "#e9f7f2", "--surface-rail": "#17332e", "--surface-rail-hi": "#245047", "--text-primary": "#122b27", "--text-secondary": "#315c54", "--text-muted": "#5f7f78", "--border-subtle": "#d9eee8", "--border-default": "#bddfd5", "--border-strong": "#8fc8b9", "--brand": "#6d4aff", "--brand-hover": "#5834e8", "--brand-active": "#4325bc", "--brand-soft": "#ebe6ff", "--brand-soft-text": "#4d32b8", "--accent": "#008c72", "--accent-soft": "#d8f5ec", "--accent-soft-text": "#006b57" },
    dark: { "--surface-canvas": "#0c1d1a", "--surface-sheet": "#132824", "--surface-sheet-2": "#1a342f", "--surface-rail": "#071310", "--surface-rail-hi": "#21423b", "--text-primary": "#eaf8f4", "--text-secondary": "#b8d8cf", "--text-muted": "#86aaa1", "--border-subtle": "#203d37", "--border-default": "#31564e", "--border-strong": "#48766b", "--brand": "#a995ff", "--brand-hover": "#c3b5ff", "--brand-active": "#ddd5ff", "--brand-soft": "#33246d", "--brand-soft-text": "#d4caff", "--accent": "#4dd6b3", "--accent-soft": "#143f35", "--accent-soft-text": "#8aead2" },
  },
})];
