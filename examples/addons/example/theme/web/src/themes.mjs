import { defineTheme } from "@angee/ui/theme-runtime";

const PAPER_TINTS = new Set(["cream", "white"]);

function parse(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("Paper theme options must be an object.");
  }
  if (!PAPER_TINTS.has(value.paperTint)) {
    throw new TypeError("Paper tint must be cream or white.");
  }
  return { paperTint: value.paperTint };
}

export const themes = [defineTheme({
  contractVersion: 1,
  id: "example.paper",
  labelKey: "paper.label",
  descriptionKey: "paper.description",
  revision: 1,
  stylesheets: ["./theme.css"],
  tokens: {
    shared: {
      "--font-family-sans": "Georgia, ui-serif, serif",
      "--r-4": "2px",
      "--r-6": "3px",
      "--r-8": "4px",
      "--r-10": "5px",
      "--r-12": "6px",
    },
    light: {
      "--surface-canvas": "#eee9dc",
      "--surface-sheet": "#fffdf7",
      "--surface-sheet-2": "#f4efe3",
      "--surface-rail": "#32302b",
      "--surface-rail-hi": "#49453d",
      "--text-primary": "#292720",
      "--text-secondary": "#575247",
      "--text-muted": "#777064",
      "--border-subtle": "#e4ddce",
      "--border-default": "#cec4b1",
      "--brand": "#315c52",
      "--brand-hover": "#244940",
      "--brand-active": "#19362f",
      "--brand-soft": "#dce9e4",
      "--brand-soft-text": "#244940",
    },
    dark: {
      "--surface-canvas": "#1e1c19",
      "--surface-sheet": "#292621",
      "--surface-sheet-2": "#353129",
      "--surface-rail": "#151411",
      "--surface-rail-hi": "#3d3931",
      "--text-primary": "#f3eee2",
      "--text-secondary": "#d3cbbb",
      "--text-muted": "#a69d8d",
      "--border-subtle": "#3b372f",
      "--border-default": "#575044",
      "--brand": "#8bc8b8",
      "--brand-hover": "#a8d8cb",
      "--brand-active": "#c4e8de",
      "--brand-soft": "#24483f",
      "--brand-soft-text": "#bce2d7",
    },
  },
  options: {
    version: 1,
    defaults: { paperTint: "cream" },
    parse,
    resolve(value) {
      const options = parse(value);
      return options.paperTint === "white"
        ? { light: { "--surface-canvas": "#f2f2f0", "--surface-sheet": "#ffffff", "--surface-sheet-2": "#f6f6f3" } }
        : { shared: {}, light: {}, dark: {} };
    },
  },
})];
