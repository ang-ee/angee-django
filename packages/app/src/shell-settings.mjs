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
    for (const key of ["themeLocked", "railExpanded"]) {
      if (value[key] !== undefined && typeof value[key] !== "boolean") throw new Error(`${where}.${key} must be true or false.`);
    }
  };
  const settings = mapping(shell, "ANGEE_UI.shell", ["brand", "site", "theme", "apps", "hosts", "app"]);
  facts(settings, "ANGEE_UI.shell");
  if (settings.site !== undefined) {
    const site = mapping(settings.site, "ANGEE_UI.shell.site", ["title", "description", "icon", "touchIcon", "image"]);
    for (const key of Object.keys(site).sort()) text(site[key], `ANGEE_UI.shell.site.${key}`);
    if (site.title === undefined && settings.brand === undefined) {
      throw new Error("ANGEE_UI.shell.site needs a title or shell.brand to supply it.");
    }
  }
  if (settings.app !== undefined) text(settings.app, "ANGEE_UI.shell.app");
  for (const [name, value] of Object.entries(mapping(settings.apps ?? {}, "ANGEE_UI.shell.apps"))) {
    const where = `ANGEE_UI.shell.apps.${name}`;
    const app = mapping(value, where, ["rail", "brand", "theme", "themeLocked", "home", "railExpanded"]);
    // No rail shows every root; a declared one names each root once.
    const rail = app.rail;
    if (rail !== undefined && (!Array.isArray(rail) || !rail.length || !rail.every((id) => typeof id === "string" && id))) {
      throw new Error(`${where}.rail must be a non-empty list of menu root ids.`);
    }
    if (rail !== undefined && new Set(rail).size !== rail.length) throw new Error(`${where}.rail lists a root twice.`);
    facts(app, where);
    if (app.home !== undefined) text(app.home, `${where}.home`);
    if (rail === undefined && app.home !== undefined) {
      throw new Error(`${where}.home needs a rail; an app without one shows every root and lands where nothing selected does.`);
    }
  }
  for (const [host, key] of Object.entries(mapping(settings.hosts ?? {}, "ANGEE_UI.shell.hosts"))) {
    if (host !== host.toLowerCase()) throw new Error(`ANGEE_UI.shell.hosts["${host}"] must be a lower-case hostname.`);
    text(key, `ANGEE_UI.shell.hosts["${host}"]`);
  }
}

/** The static head validated shell settings ask for: a brand alone gives only the title. */
export function shellSiteHead({ site, brand }) {
  if (site === undefined) return brand && { title: brand.name };
  const { title = brand.name, ...facts } = site;
  return { title, site: { ...facts, ...(brand ? { siteName: brand.name } : {}) } };
}
