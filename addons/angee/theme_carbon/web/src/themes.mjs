import { defineTheme } from "@angee/ui/theme-runtime";
export const themes = [defineTheme({
  contractVersion: 1, id: "angee.carbon", labelKey: "carbon.label", descriptionKey: "carbon.description", revision: 1,
  tokens: {
    shared: { "--r-2": "0px", "--r-4": "0px", "--r-6": "2px", "--r-8": "2px", "--r-10": "2px", "--r-12": "2px", "--control-h-sm": "24px", "--control-h-md": "32px", "--control-h-lg": "40px", "--font-family-sans": "IBM Plex Sans, Arial, sans-serif" },
    light: { "--surface-canvas": "#f4f4f4", "--surface-sheet": "#ffffff", "--surface-sheet-2": "#e8e8e8", "--surface-rail": "#161616", "--surface-rail-hi": "#353535", "--text-primary": "#161616", "--text-secondary": "#525252", "--text-muted": "#6f6f6f", "--border-subtle": "#e0e0e0", "--border-default": "#c6c6c6", "--border-strong": "#8d8d8d", "--brand": "#0f62fe", "--brand-hover": "#0353e9", "--brand-active": "#002d9c", "--brand-soft": "#d0e2ff", "--brand-soft-text": "#0043ce", "--text-link": "#0f62fe" },
    dark: { "--surface-canvas": "#161616", "--surface-sheet": "#262626", "--surface-sheet-2": "#353535", "--surface-rail": "#0f0f0f", "--surface-rail-hi": "#393939", "--text-primary": "#f4f4f4", "--text-secondary": "#c6c6c6", "--text-muted": "#a8a8a8", "--border-subtle": "#393939", "--border-default": "#525252", "--border-strong": "#6f6f6f", "--brand": "#78a9ff", "--brand-hover": "#a6c8ff", "--brand-active": "#d0e2ff", "--brand-soft": "#001d6c", "--brand-soft-text": "#a6c8ff", "--text-link": "#78a9ff" },
  },
})];
