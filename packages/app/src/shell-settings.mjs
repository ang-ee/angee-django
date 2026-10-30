/** A brand names its product and a glyph; the glyph's registration is checked at app boot. */
export function assertBrand(where, brand) {
  const fields = typeof brand === "object" && brand !== null && !Array.isArray(brand) ? brand : undefined;
  if (!fields || Object.keys(fields).some((key) => key !== "name" && key !== "mark")
    || [fields.name, fields.mark].some((value) => typeof value !== "string" || !value.trim())) {
    throw new Error(`${where}: brand must be { name, mark } with a non-empty name and mark.`);
  }
}

/** Refuse a malformed `ANGEE_UI.shell`, which would otherwise select the wrong app silently. */
export function assertShellSettings(shell) {
  const mapping = (value, where, keys) => {
    if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error(`${where} must be a mapping.`);
    const unknown = keys ? Object.keys(value).find((key) => !keys.includes(key)) : undefined;
    if (unknown !== undefined) throw new Error(`${where} has unknown key "${unknown}".`);
    return value;
  };
  const text = (value, where) => {
    if (typeof value !== "string" || !value.trim()) throw new Error(`${where} must be a non-empty string.`);
  };
  const facts = (value, where) => {
    if (value.brand !== undefined) assertBrand(where, value.brand);
    if (value.theme !== undefined) text(value.theme, `${where}.theme`);
    if (value.railExpanded !== undefined && typeof value.railExpanded !== "boolean") {
      throw new Error(`${where}.railExpanded must be true or false.`);
    }
  };
  const settings = mapping(shell, "ANGEE_UI.shell", ["brand", "theme", "apps", "hosts"]);
  facts(settings, "ANGEE_UI.shell");
  for (const [name, value] of Object.entries(mapping(settings.apps ?? {}, "ANGEE_UI.shell.apps"))) {
    const where = `ANGEE_UI.shell.apps.${name}`;
    const app = mapping(value, where, ["rail", "brand", "theme", "home", "railExpanded"]);
    const rail = app.rail;
    if (!Array.isArray(rail) || !rail.length || !rail.every((id) => typeof id === "string" && id)) {
      throw new Error(`${where}.rail must be a non-empty list of menu root ids.`);
    }
    if (new Set(rail).size !== rail.length) throw new Error(`${where}.rail lists a root twice.`);
    facts(app, where);
    if (app.home !== undefined) text(app.home, `${where}.home`);
  }
  for (const [host, key] of Object.entries(mapping(settings.hosts ?? {}, "ANGEE_UI.shell.hosts"))) {
    if (host !== host.toLowerCase()) throw new Error(`ANGEE_UI.shell.hosts["${host}"] must be a lower-case hostname.`);
    text(key, `ANGEE_UI.shell.hosts["${host}"]`);
  }
}
