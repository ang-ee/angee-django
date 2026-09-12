import { defineTheme } from "@angee/ui/theme-runtime";
const HEX = /^#[0-9a-f]{6}$/i;
const RADII = new Set(["0px", "4px", "6px", "8px", "12px"]);
function parse(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError("Brand options must be an object.");
  const brand = HEX.test(value.brand) ? value.brand.toLowerCase() : null;
  const accent = HEX.test(value.accent) ? value.accent.toLowerCase() : null;
  const radius = RADII.has(value.radius) ? value.radius : null;
  if (!brand || !accent || !radius) throw new TypeError("Brand options require six-digit brand/accent colors and an approved radius.");
  return { brand, accent, radius };
}
function onColor(hex) {
  const channels = [hex.slice(1, 3), hex.slice(3, 5), hex.slice(5, 7)].map((part) => Number.parseInt(part, 16));
  const luminance = (channels[0] * 299 + channels[1] * 587 + channels[2] * 114) / 1000;
  return luminance > 150 ? "#11141a" : "#ffffff";
}
export const themes = [defineTheme({
  contractVersion: 1, id: "angee.brand", labelKey: "brand.label", descriptionKey: "brand.description", revision: 1,
  tokens: { shared: {}, light: {}, dark: {} },
  options: {
    version: 1, defaults: { brand: "#5b5bd6", accent: "#0d9488", radius: "6px" }, parse,
    resolve(value) { const options = parse(value); return { shared: { "--brand": options.brand, "--brand-hover": options.brand, "--brand-active": options.brand, "--text-on-brand": onColor(options.brand), "--accent": options.accent, "--r-4": options.radius, "--r-6": options.radius, "--r-8": options.radius, "--r-10": options.radius, "--r-12": options.radius } }; },
  },
})];
