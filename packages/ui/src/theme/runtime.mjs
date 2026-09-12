/** Pure theme contract shared by addon entries, Node codegen, and browsers. */
export const THEME_CONTRACT_VERSION = 1;
export const THEME_ID_PATTERN = /^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$/;
export const THEME_TOKEN_NAMES = Object.freeze([
  "--surface-canvas", "--surface-sheet", "--surface-sheet-2", "--surface-rail",
  "--surface-rail-hi", "--surface-popover", "--surface-inset", "--text-primary",
  "--text-secondary", "--text-muted", "--text-subtle", "--text-inverse",
  "--text-on-rail", "--text-on-rail-mut", "--text-on-rail-hi", "--text-on-brand",
  "--text-link", "--border-subtle", "--border-default", "--border-strong",
  "--border-focus", "--border-on-rail", "--brand", "--brand-hover",
  "--brand-active", "--brand-soft", "--brand-soft-text", "--accent",
  "--accent-soft", "--accent-soft-text", "--success-soft", "--success-text",
  "--warning-soft", "--warning-text", "--danger-soft", "--danger-text",
  "--info-soft", "--info-text", "--success", "--on-success", "--success-line",
  "--success-tint", "--warning", "--on-warning", "--warning-line", "--warning-tint",
  "--danger", "--danger-hover", "--danger-active", "--on-danger", "--danger-line",
  "--danger-tint", "--info", "--on-info", "--info-line", "--info-tint",
  "--on-accent", "--accent-line", "--accent-tint", "--brand-line", "--brand-tint",
  "--ring", "--ring-danger", "--font-family-sans",
  "--font-family-mono", "--elevation-xs", "--elevation-sm", "--elevation-md",
  "--elevation-lg", "--elevation-popover", "--r-2", "--r-4", "--r-6", "--r-8", "--r-10", "--r-12",
  "--r-full", "--rail-w", "--topbar-h", "--controlpanel-h", "--chatter-w",
  "--control-h-sm", "--control-h-md", "--control-h-lg",
]);

const TOKEN_SET = new Set(THEME_TOKEN_NAMES);
const FORBIDDEN_VALUE = /[;{}@]|url\s*\(|expression\s*\(|!important/i;

export function defineTheme(definition) {
  assertThemeDefinition(definition);
  return definition;
}

export function assertThemeDefinition(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError("Theme definition must be an object.");
  if (value.contractVersion !== THEME_CONTRACT_VERSION) throw new TypeError(`Theme ${String(value.id ?? "<unknown>")} uses an unsupported contractVersion.`);
  if (typeof value.id !== "string" || !THEME_ID_PATTERN.test(value.id)) throw new TypeError(`Theme id ${JSON.stringify(value.id)} is invalid.`);
  for (const field of ["labelKey", "descriptionKey"]) {
    if (typeof value[field] !== "string" || value[field].length === 0 || value[field].length > 160) throw new TypeError(`Theme ${value.id} must declare a bounded ${field}.`);
  }
  if (!Number.isSafeInteger(value.revision) || value.revision < 1) throw new TypeError(`Theme ${value.id} revision must be a positive integer.`);
  if (!value.tokens || typeof value.tokens !== "object" || Array.isArray(value.tokens)) throw new TypeError(`Theme ${value.id} must declare token layers.`);
  for (const layer of ["shared", "light", "dark"]) assertTokenLayer(value.tokens[layer], `${value.id}.${layer}`);
  if (value.stylesheets !== undefined) {
    if (!Array.isArray(value.stylesheets) || value.stylesheets.length > 16) throw new TypeError(`Theme ${value.id} stylesheets must be a bounded array.`);
    for (const stylesheet of value.stylesheets) {
      if (typeof stylesheet !== "string" || !stylesheet.startsWith("./") || stylesheet.includes("..") || !stylesheet.endsWith(".css")) throw new TypeError(`Theme ${value.id} stylesheet ${JSON.stringify(stylesheet)} must be a relative CSS path.`);
    }
  }
  if (value.options !== undefined) assertThemeOptions(value.id, value.options);
  return value;
}

export function assertThemeCatalogue(definitions) {
  if (!Array.isArray(definitions)) throw new TypeError("Theme catalogue must be an array.");
  const seen = new Set();
  for (const definition of definitions) {
    assertThemeDefinition(definition);
    if (seen.has(definition.id)) throw new TypeError(`Duplicate installed theme id ${JSON.stringify(definition.id)}.`);
    seen.add(definition.id);
  }
  return [...definitions].sort((left, right) => left.id < right.id ? -1 : left.id > right.id ? 1 : 0);
}

export function resolveThemeOptions(definition, envelope) {
  assertThemeDefinition(definition);
  if (!definition.options) return { version: 0, value: Object.freeze({}), tokens: definition.tokens };
  const version = Number.isSafeInteger(envelope?.version) ? envelope.version : definition.options.version;
  let raw = envelope?.value ?? definition.options.defaults;
  if (version !== definition.options.version) {
    if (version < definition.options.version && typeof definition.options.migrate === "function") raw = definition.options.migrate(raw, version);
    else throw new TypeError(`Theme ${definition.id} options version ${version} is unsupported.`);
  }
  const value = definition.options.parse(raw);
  const resolved = definition.options.resolve(value);
  const tokens = {
    shared: { ...definition.tokens.shared, ...(resolved.shared ?? {}) },
    light: { ...definition.tokens.light, ...(resolved.light ?? {}) },
    dark: { ...definition.tokens.dark, ...(resolved.dark ?? {}) },
  };
  for (const layer of ["shared", "light", "dark"]) assertTokenLayer(tokens[layer], `${definition.id}.options.${layer}`);
  return { version: definition.options.version, value, tokens };
}

export function compileThemeCss(definitions) {
  const css = assertThemeCatalogue(definitions).map((definition) => [
    renderRule(`[data-theme-id="${definition.id}"]`, definition.tokens.shared),
    renderRule(`[data-theme-id="${definition.id}"][data-color-scheme="light"]`, definition.tokens.light),
    renderRule(`[data-theme-id="${definition.id}"][data-color-scheme="dark"]`, definition.tokens.dark),
  ].filter(Boolean).join("\n")).filter(Boolean).join("\n\n");
  return css ? `${css}\n` : "";
}

export function serializableThemeMetadata(definition) {
  assertThemeDefinition(definition);
  return {
    contractVersion: definition.contractVersion, id: definition.id,
    labelKey: definition.labelKey, descriptionKey: definition.descriptionKey,
    revision: definition.revision, optionsVersion: definition.options?.version ?? null,
    optionDefaults: definition.options?.defaults ?? null,
  };
}

const HEX_COLOR = /^#[0-9a-f]{6}$/i;
const CUSTOMIZATION_KEYS = Object.freeze([
  "brand", "accent", "neutral", "canvas", "surface", "rail",
  "success", "warning", "danger", "info",
  "font", "radius", "density", "elevation", "logo",
]);
const CUSTOMIZATION_KEY_SET = new Set(CUSTOMIZATION_KEYS);
const FONT_STACKS = Object.freeze({
  system: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  inter: 'Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  humanist: '"Avenir Next", "Segoe UI", system-ui, sans-serif',
  industrial: '"IBM Plex Sans", Arial, sans-serif',
  editorial: 'Georgia, "Times New Roman", serif',
  mono: '"JetBrains Mono", "SFMono-Regular", Consolas, monospace',
});
const RADIUS_TOKENS = Object.freeze({
  square: ["0px", "0px", "0px", "0px", "0px", "0px"],
  compact: ["1px", "2px", "4px", "6px", "8px", "10px"],
  standard: ["2px", "4px", "6px", "8px", "10px", "12px"],
  soft: ["4px", "6px", "8px", "10px", "12px", "14px"],
  round: ["6px", "8px", "12px", "16px", "20px", "24px"],
});
const DENSITY_TOKENS = Object.freeze({
  compact: ["24px", "28px", "34px"],
  balanced: ["26px", "32px", "38px"],
  comfortable: ["28px", "34px", "40px"],
  spacious: ["30px", "38px", "44px"],
});
const ELEVATION_TOKENS = Object.freeze({
  flat: {
    light: ["none", "none", "0 0 0 1px #00000014", "0 0 0 1px #0000001f", "0 0 0 1px #00000029"],
    dark: ["none", "none", "0 0 0 1px #ffffff1a", "0 0 0 1px #ffffff24", "0 0 0 1px #ffffff2e"],
  },
  subtle: {
    light: ["0 1px 1px #0000000a", "0 1px 2px #0000000f", "0 4px 12px #00000014", "0 12px 32px #0000001f", "0 6px 16px #0000001a"],
    dark: ["0 1px 1px #0000004d", "0 1px 2px #00000066", "0 4px 12px #00000070", "0 12px 32px #00000080", "0 6px 16px #00000073"],
  },
  soft: {
    light: ["0 1px 2px #0000000a", "0 2px 5px #00000012", "0 8px 24px #0000001a", "0 20px 48px #00000024", "0 12px 32px #0000001f"],
    dark: ["0 1px 2px #00000066", "0 2px 6px #00000073", "0 8px 24px #00000080", "0 20px 48px #00000094", "0 12px 32px #00000085"],
  },
  dramatic: {
    light: ["0 2px 4px #00000012", "0 4px 10px #0000001a", "0 14px 36px #00000029", "0 28px 64px #00000038", "0 18px 44px #00000033"],
    dark: ["0 2px 4px #00000073", "0 4px 10px #00000080", "0 14px 36px #00000099", "0 28px 64px #000000ad", "0 18px 44px #000000a3"],
  },
});
const FONT_KEYS = new Set(["theme", ...Object.keys(FONT_STACKS)]);
const RADIUS_KEYS = new Set(["theme", ...Object.keys(RADIUS_TOKENS)]);
const DENSITY_KEYS = new Set(["theme", ...Object.keys(DENSITY_TOKENS)]);
const ELEVATION_KEYS = new Set(["theme", ...Object.keys(ELEVATION_TOKENS)]);
const LOGO_KEYS = new Set(["theme", "brand", "accent", "mono", "star", "corner"]);

/**
 * Build the bounded option contract shared by customizable theme addons.
 * Defaults describe the authored base; equal values emit no overrides, so the
 * base theme remains byte-for-byte authoritative until a user changes a field.
 */
export function createThemeCustomizationOptions(defaults, configuration = {}) {
  const normalizedDefaults = parseThemeCustomization(defaults);
  const version = configuration.version ?? 1;
  if (!Number.isSafeInteger(version) || version < 1) throw new TypeError("Theme customization version must be a positive integer.");
  if (configuration.migrate !== undefined && typeof configuration.migrate !== "function") throw new TypeError("Theme customization migrate must be a function.");
  return {
    version,
    defaults: normalizedDefaults,
    parse: parseThemeCustomization,
    ...(configuration.migrate ? {
      migrate(value, fromVersion) {
        return parseThemeCustomization(configuration.migrate(value, fromVersion, normalizedDefaults));
      },
    } : {}),
    resolve(value) {
      return resolveThemeCustomization(parseThemeCustomization(value), normalizedDefaults);
    },
  };
}

/** Fill fields introduced by a newer shared customization schema from a base theme. */
export function migrateThemeCustomization(value, defaults) {
  const normalizedDefaults = parseThemeCustomization(defaults);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError("Legacy theme customization must be an object.");
  const keys = Object.keys(value);
  if (keys.some((key) => !CUSTOMIZATION_KEY_SET.has(key))) throw new TypeError("Legacy theme customization contains unsupported fields.");
  return parseThemeCustomization({ ...normalizedDefaults, ...value });
}

export function parseThemeCustomization(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError("Theme customization must be an object.");
  const keys = Object.keys(value);
  if (keys.length !== CUSTOMIZATION_KEYS.length || keys.some((key) => !CUSTOMIZATION_KEY_SET.has(key))) {
    throw new TypeError("Theme customization must contain only the complete supported design fields.");
  }
  const brand = normalizedHex(value.brand, "brand");
  const accent = normalizedHex(value.accent, "accent");
  const neutral = normalizedHex(value.neutral, "neutral");
  const canvas = normalizedHex(value.canvas, "canvas");
  const surface = normalizedHex(value.surface, "surface");
  const rail = normalizedHex(value.rail, "rail");
  const success = normalizedHex(value.success, "success");
  const warning = normalizedHex(value.warning, "warning");
  const danger = normalizedHex(value.danger, "danger");
  const info = normalizedHex(value.info, "info");
  if (!FONT_KEYS.has(value.font)) throw new TypeError("Theme customization font is unsupported.");
  if (!RADIUS_KEYS.has(value.radius)) throw new TypeError("Theme customization radius is unsupported.");
  if (!DENSITY_KEYS.has(value.density)) throw new TypeError("Theme customization density is unsupported.");
  if (!ELEVATION_KEYS.has(value.elevation)) throw new TypeError("Theme customization elevation is unsupported.");
  if (!LOGO_KEYS.has(value.logo)) throw new TypeError("Theme customization logo is unsupported.");
  return {
    brand, accent, neutral, canvas, surface, rail,
    success, warning, danger, info,
    font: value.font, radius: value.radius, density: value.density,
    elevation: value.elevation, logo: value.logo,
  };
}

function resolveThemeCustomization(value, defaults) {
  const shared = {};
  const light = {};
  const dark = {};
  if (value.brand !== defaults.brand) applyBrandPalette(value.brand, light, dark);
  if (value.accent !== defaults.accent) applyAccentPalette(value.accent, light, dark);
  if (value.neutral !== defaults.neutral) applyNeutralPalette(value.neutral, light, dark);
  if (value.canvas !== defaults.canvas) applyCanvasPalette(value.canvas, light, dark);
  if (value.surface !== defaults.surface) applySurfacePalette(value.surface, light, dark);
  if (value.rail !== defaults.rail) applyRailPalette(value.rail, light, dark);
  for (const role of ["success", "warning", "danger", "info"]) {
    if (value[role] !== defaults[role]) applyStatusPalette(role, value[role], light, dark);
  }
  if (value.font !== defaults.font && value.font !== "theme") shared["--font-family-sans"] = FONT_STACKS[value.font];
  if (value.radius !== defaults.radius && value.radius !== "theme") assignScale(shared, ["--r-2", "--r-4", "--r-6", "--r-8", "--r-10", "--r-12"], RADIUS_TOKENS[value.radius]);
  if (value.density !== defaults.density && value.density !== "theme") assignScale(shared, ["--control-h-sm", "--control-h-md", "--control-h-lg"], DENSITY_TOKENS[value.density]);
  if (value.elevation !== defaults.elevation && value.elevation !== "theme") {
    const elevation = ELEVATION_TOKENS[value.elevation];
    const names = ["--elevation-xs", "--elevation-sm", "--elevation-md", "--elevation-lg", "--elevation-popover"];
    assignScale(light, names, elevation.light);
    assignScale(dark, names, elevation.dark);
  }
  return { shared, light, dark };
}

function applyBrandPalette(color, light, dark) {
  const lightSoft = mixHex(color, "#ffffff", 0.88);
  const darkBrand = mixHex(color, "#ffffff", 0.16);
  const darkSoft = mixHex(color, "#0b0f14", 0.76);
  Object.assign(light, {
    "--brand": color,
    "--brand-hover": mixHex(color, "#000000", 0.14),
    "--brand-active": mixHex(color, "#000000", 0.28),
    "--brand-soft": lightSoft,
    "--brand-soft-text": readableTintText(color, lightSoft),
    "--text-on-brand": readableText(color),
    "--brand-line": mixHex(color, "#ffffff", 0.48),
    "--brand-tint": mixHex(color, "#ffffff", 0.94),
    "--text-link": readableTintText(color, "#ffffff"),
    "--border-focus": color,
    "--ring": `0 0 0 3px ${withAlpha(color, 0.30)}`,
  });
  Object.assign(dark, {
    "--brand": darkBrand,
    "--brand-hover": mixHex(color, "#ffffff", 0.30),
    "--brand-active": mixHex(color, "#ffffff", 0.44),
    "--brand-soft": darkSoft,
    "--brand-soft-text": readableTintText(darkBrand, darkSoft),
    "--text-on-brand": readableText(darkBrand),
    "--brand-line": withAlpha(darkBrand, 0.40),
    "--brand-tint": mixHex(darkBrand, "#0b0f14", 0.82),
    "--text-link": readableTintText(darkBrand, "#11141a"),
    "--border-focus": darkBrand,
    "--ring": `0 0 0 3px ${withAlpha(darkBrand, 0.35)}`,
  });
}

function applyAccentPalette(color, light, dark) {
  const lightSoft = mixHex(color, "#ffffff", 0.88);
  const darkAccent = mixHex(color, "#ffffff", 0.16);
  const darkSoft = mixHex(color, "#0b0f14", 0.76);
  Object.assign(light, {
    "--accent": color,
    "--on-accent": readableText(color),
    "--accent-soft": lightSoft,
    "--accent-soft-text": readableTintText(color, lightSoft),
    "--accent-line": mixHex(color, "#ffffff", 0.48),
    "--accent-tint": mixHex(color, "#ffffff", 0.94),
  });
  Object.assign(dark, {
    "--accent": darkAccent,
    "--on-accent": readableText(darkAccent),
    "--accent-soft": darkSoft,
    "--accent-soft-text": readableTintText(darkAccent, darkSoft),
    "--accent-line": withAlpha(darkAccent, 0.40),
    "--accent-tint": mixHex(darkAccent, "#0b0f14", 0.82),
  });
}

function applyCanvasPalette(color, light, dark) {
  light["--surface-canvas"] = color;
  dark["--surface-canvas"] = mixHex(color, "#000000", 0.94);
}

function applySurfacePalette(color, light, dark) {
  const lightInset = mixHex(color, "#000000", 0.06);
  const darkSurface = mixHex(color, "#000000", 0.88);
  Object.assign(light, {
    "--surface-sheet": color,
    "--surface-sheet-2": mixHex(color, "#000000", 0.025),
    "--surface-popover": color,
    "--surface-inset": lightInset,
  });
  Object.assign(dark, {
    "--surface-sheet": darkSurface,
    "--surface-sheet-2": mixHex(darkSurface, "#ffffff", 0.055),
    "--surface-popover": mixHex(darkSurface, "#ffffff", 0.055),
    "--surface-inset": mixHex(darkSurface, "#ffffff", 0.035),
  });
}

function applyRailPalette(color, light, dark) {
  assignRailPalette(light, color);
  assignRailPalette(dark, mixHex(color, "#000000", 0.18));
}

function assignRailPalette(target, color) {
  const foreground = readableText(color);
  const highlightTarget = foreground === "#ffffff" ? "#ffffff" : "#000000";
  Object.assign(target, {
    "--surface-rail": color,
    "--surface-rail-hi": mixHex(color, highlightTarget, 0.12),
    "--text-on-rail": mixHex(foreground, color, 0.16),
    "--text-on-rail-mut": mixHex(foreground, color, 0.40),
    "--text-on-rail-hi": foreground,
    "--border-on-rail": mixHex(foreground, color, 0.82),
  });
}

function applyStatusPalette(role, color, light, dark) {
  const darkColor = mixHex(color, "#ffffff", 0.14);
  const lightSoft = mixHex(color, "#ffffff", 0.86);
  const darkSoft = mixHex(color, "#0b0f14", 0.76);
  Object.assign(light, {
    [`--${role}`]: color,
    [`--on-${role}`]: readableText(color),
    [`--${role}-soft`]: lightSoft,
    [`--${role}-text`]: readableTintText(color, lightSoft),
    [`--${role}-line`]: mixHex(color, "#ffffff", 0.48),
    [`--${role}-tint`]: mixHex(color, "#ffffff", 0.94),
  });
  Object.assign(dark, {
    [`--${role}`]: darkColor,
    [`--on-${role}`]: readableText(darkColor),
    [`--${role}-soft`]: darkSoft,
    [`--${role}-text`]: readableTintText(darkColor, darkSoft),
    [`--${role}-line`]: withAlpha(darkColor, 0.40),
    [`--${role}-tint`]: mixHex(darkColor, "#0b0f14", 0.82),
  });
  if (role === "danger") {
    light["--danger-hover"] = mixHex(color, "#000000", 0.14);
    light["--danger-active"] = mixHex(color, "#000000", 0.28);
    light["--ring-danger"] = `0 0 0 3px ${withAlpha(color, 0.30)}`;
    dark["--danger-hover"] = mixHex(color, "#ffffff", 0.30);
    dark["--danger-active"] = mixHex(color, "#ffffff", 0.44);
    dark["--ring-danger"] = `0 0 0 3px ${withAlpha(darkColor, 0.35)}`;
  }
}

function applyNeutralPalette(color, light, dark) {
  const lightRail = mixHex(color, "#000000", 0.82);
  const darkRail = mixHex(color, "#000000", 0.90);
  Object.assign(light, {
    "--surface-canvas": mixHex(color, "#ffffff", 0.92),
    "--surface-sheet": mixHex(color, "#ffffff", 0.98),
    "--surface-sheet-2": mixHex(color, "#ffffff", 0.95),
    "--surface-inset": mixHex(color, "#ffffff", 0.87),
    "--surface-popover": mixHex(color, "#ffffff", 0.98),
    "--surface-rail": lightRail,
    "--surface-rail-hi": mixHex(color, "#000000", 0.68),
    "--text-primary": mixHex(color, "#000000", 0.78),
    "--text-secondary": mixHex(color, "#000000", 0.58),
    "--text-muted": mixHex(color, "#000000", 0.30),
    "--text-subtle": mixHex(color, "#ffffff", 0.24),
    "--text-inverse": readableText(lightRail),
    "--text-on-rail": readableTintText(mixHex(color, "#ffffff", 0.58), lightRail),
    "--text-on-rail-mut": readableTintText(mixHex(color, "#ffffff", 0.34), lightRail),
    "--text-on-rail-hi": readableText(lightRail),
    "--border-subtle": mixHex(color, "#ffffff", 0.78),
    "--border-default": mixHex(color, "#ffffff", 0.65),
    "--border-strong": mixHex(color, "#ffffff", 0.38),
    "--border-on-rail": mixHex(color, "#000000", 0.62),
  });
  Object.assign(dark, {
    "--surface-canvas": mixHex(color, "#000000", 0.84),
    "--surface-sheet": mixHex(color, "#000000", 0.76),
    "--surface-sheet-2": mixHex(color, "#000000", 0.68),
    "--surface-inset": mixHex(color, "#000000", 0.72),
    "--surface-popover": mixHex(color, "#000000", 0.66),
    "--surface-rail": darkRail,
    "--surface-rail-hi": mixHex(color, "#000000", 0.66),
    "--text-primary": mixHex(color, "#ffffff", 0.84),
    "--text-secondary": mixHex(color, "#ffffff", 0.66),
    "--text-muted": mixHex(color, "#ffffff", 0.46),
    "--text-subtle": mixHex(color, "#ffffff", 0.30),
    "--text-inverse": readableText(mixHex(color, "#ffffff", 0.84)),
    "--text-on-rail": readableTintText(mixHex(color, "#ffffff", 0.72), darkRail),
    "--text-on-rail-mut": readableTintText(mixHex(color, "#ffffff", 0.48), darkRail),
    "--text-on-rail-hi": readableText(darkRail),
    "--border-subtle": mixHex(color, "#000000", 0.60),
    "--border-default": mixHex(color, "#000000", 0.42),
    "--border-strong": mixHex(color, "#000000", 0.20),
    "--border-on-rail": mixHex(color, "#000000", 0.64),
  });
}

function normalizedHex(value, name) {
  if (typeof value !== "string" || !HEX_COLOR.test(value)) throw new TypeError(`Theme customization ${name} must be a six-digit hex color.`);
  return value.toLowerCase();
}

function assignScale(target, names, values) {
  names.forEach((name, index) => { target[name] = values[index]; });
}

function channels(hex) {
  return [hex.slice(1, 3), hex.slice(3, 5), hex.slice(5, 7)].map((part) => Number.parseInt(part, 16));
}

function mixHex(from, to, toWeight) {
  const left = channels(from);
  const right = channels(to);
  return `#${left.map((channel, index) => Math.round(channel * (1 - toWeight) + right[index] * toWeight).toString(16).padStart(2, "0")).join("")}`;
}

function withAlpha(hex, opacity) {
  return `${hex}${Math.round(opacity * 255).toString(16).padStart(2, "0")}`;
}

function relativeLuminance(hex) {
  const values = channels(hex).map((channel) => {
    const value = channel / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return values[0] * 0.2126 + values[1] * 0.7152 + values[2] * 0.0722;
}

function contrastRatio(left, right) {
  const first = relativeLuminance(left);
  const second = relativeLuminance(right);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

function readableText(background) {
  return contrastRatio("#11141a", background) >= contrastRatio("#ffffff", background) ? "#11141a" : "#ffffff";
}

function readableTintText(color, background) {
  if (contrastRatio(color, background) >= 4.5) return color;
  const target = readableText(background);
  for (let step = 1; step <= 10; step += 1) {
    const candidate = mixHex(color, target, step / 10);
    if (contrastRatio(candidate, background) >= 4.5) return candidate;
  }
  return target;
}

function assertTokenLayer(layer, owner) {
  if (!layer || typeof layer !== "object" || Array.isArray(layer)) throw new TypeError(`Theme token layer ${owner} must be an object.`);
  const entries = Object.entries(layer);
  if (entries.length > THEME_TOKEN_NAMES.length) throw new TypeError(`Theme token layer ${owner} has too many tokens.`);
  for (const [name, value] of entries) {
    if (!TOKEN_SET.has(name)) throw new TypeError(`Theme token ${name} in ${owner} is not public.`);
    if (typeof value !== "string" || value.length === 0 || value.length > 256 || FORBIDDEN_VALUE.test(value)) throw new TypeError(`Theme token ${name} in ${owner} has an unsafe value.`);
  }
}

function assertThemeOptions(id, options) {
  if (!options || typeof options !== "object" || Array.isArray(options)) throw new TypeError(`Theme ${id} options must be an object.`);
  if (!Number.isSafeInteger(options.version) || options.version < 1) throw new TypeError(`Theme ${id} options version must be a positive integer.`);
  if (typeof options.parse !== "function" || typeof options.resolve !== "function") throw new TypeError(`Theme ${id} options must provide parse and resolve functions.`);
  options.parse(options.defaults);
}

function renderRule(selector, layer) {
  const entries = Object.entries(layer).sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0);
  if (entries.length === 0) return "";
  return `${selector} {\n${entries.map(([name, value]) => `  ${name}: ${value};`).join("\n")}\n}`;
}
