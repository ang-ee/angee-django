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
  "--info-soft", "--info-text", "--ring", "--ring-danger", "--font-family-sans",
  "--font-family-mono", "--r-2", "--r-4", "--r-6", "--r-8", "--r-10", "--r-12",
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
