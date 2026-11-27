import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { assertThemeStylesheet } from "@angee/app/web-codegen";
import { isShadowList, isThemeCustomizationOptions, resolveThemeOptions, THEME_ELEVATION_PRESETS } from "@angee/ui/theme-runtime";

import { compileUtilities, root } from "./catalog.mjs";

// The public stylesheet as a host compiles it (`tailwindcss`, then `@angee/ui/styles.css`) with the
// installed Tailwind, built for the given class candidates only. A compiler's `build` adds to the
// candidates it has seen, so each call compiles afresh.
const compiledCss = async candidates => (await compileUtilities()).build(candidates);

/** The declarations of the first rule whose selector contains `selector`, with whitespace collapsed. */
function ruleBody(css, selector) {
  const at = css.indexOf(selector);
  if (at < 0) return null;
  const open = css.indexOf("{", at);
  let depth = 0;
  for (let index = open; index < css.length; index += 1) {
    if (css[index] === "{") depth += 1;
    if (css[index] === "}" && (depth -= 1) === 0) return css.slice(open + 1, index).replace(/\s+/g, " ").trim();
  }
  return null;
}

/**
 * A ring rule draws Tailwind's ring layer: it writes `--tw-ring-shadow` (the last write wins) and the
 * composed `box-shadow`, which also reads the element's own shadow layer (`--tw-shadow`) and so keeps
 * it; and it paints a transparent outline in forced colours, where every box-shadow is dropped, and
 * no outline otherwise.
 */
function assertRing(body, shadow, label) {
  assert.notEqual(body, null, `no rule for ${label} in the compiled stylesheet`);
  const forced = body.match(/@media \(forced-colors: active\) \{([^{}]*)\}/)?.[1] ?? "";
  const own = new Map([...body.replace(/@[^{]+\{[^{}]*\}/g, "").matchAll(/([\w-]+): ([^;]+);/g)].map(([, property, value]) => [property, value.trim()]));
  assert.equal(own.get("--tw-ring-shadow"), shadow, `${label} writes the ring layer`);
  assert.match(own.get("box-shadow") ?? "", /var\(--tw-ring-shadow\).*var\(--tw-shadow\)/, `${label} composes the ring with the element's own shadow`);
  assert.ok(!own.has("--tw-shadow"), `${label} leaves the element's own shadow alone`);
  assert.ok(![...own.keys()].some(property => property.startsWith("outline")), `${label} paints no outline outside forced colours`);
  assert.match(forced, /(?:^|\s)outline: 2px solid transparent;/, `${label} gives forced colours a 2px transparent outline`);
  assert.match(forced, /(?:^|\s)outline-offset: 2px;/, `${label} sets that outline 2px out`);
}

// A class used with a variant must be a registered utility (`@utility`): Tailwind gives variants
// to utilities only, and a plain class in `@layer utilities` compiles to nothing under one.
const RINGS = [
  ["focus-ring", "var(--ring)"],
  ["focus-ring-danger", "var(--ring-danger)"],
];
const VARIANTS = [
  ["focus-visible", ":focus-visible"],
  ["focus", ":focus"],
  ["focus-within", ":focus-within"],
];

for (const [ring, shadow] of RINGS) {
  test(`the ${ring} class draws its ring`, async () => {
    const css = await compiledCss([ring]);
    assertRing(ruleBody(css, `.${ring} {`) ?? ruleBody(css, `.${ring}{`), shadow, ring);
  });

  for (const [variant, pseudo] of VARIANTS) {
    test(`${variant}:${ring} compiles to a rule that draws the ring on ${pseudo}`, async () => {
      const css = await compiledCss([`${variant}:${ring}`]);
      const body = ruleBody(css, `.${variant}\\:${ring}`);
      assertRing(body, shadow, `${variant}:${ring}`);
      assert.ok(body.includes(pseudo) || css.includes(`.${variant}\\:${ring}${pseudo}`), `the rule is not scoped to ${pseudo}`);
    });
  }
}

// The other shapes the packages spell: a ring while a control's popup is open, the card frame's ring
// while its body has keyboard focus, and the danger ring of an invalid control that has focus.
for (const [candidate, selector, shadow] of [
  ["data-[popup-open]:focus-ring", ".data-\\[popup-open\\]\\:focus-ring", "var(--ring)"],
  ["has-[[data-slot=card-body]:focus-visible]:focus-ring", ".has-\\[\\[data-slot\\=card-body\\]\\:focus-visible\\]\\:focus-ring", "var(--ring)"],
  ["data-[invalid]:focus:focus-ring-danger", ".data-\\[invalid\\]\\:focus\\:focus-ring-danger", "var(--ring-danger)"],
]) {
  test(`${candidate} compiles to a rule that draws the ring`, async () => {
    assertRing(ruleBody(await compiledCss([candidate]), selector), shadow, candidate);
  });
}

test("an elevation shadow and the ring write two layers of one box-shadow, so a focused element keeps both", async () => {
  for (const elevation of ["shadow-xs", "shadow-sm", "shadow-popover"]) {
    const css = await compiledCss([`data-[active]:${elevation}`, "focus-visible:focus-ring"]);
    const own = ruleBody(css, `.data-\\[active\\]\\:${elevation}`);
    const ring = ruleBody(css, ".focus-visible\\:focus-ring");
    assert.match(own, /--tw-shadow: /, elevation);
    assert.doesNotMatch(own, /--tw-ring-shadow:/, elevation);
    const boxShadow = body => body.match(/box-shadow: ([^;]+);/)?.[1];
    assert.equal(boxShadow(own), boxShadow(ring), `${elevation} and the ring write the same composed box-shadow`);
  }
});

test("shadow-none clears only the element's own shadow; ring-0 removes a ring", async () => {
  const css = await compiledCss(["[&>input:focus]:shadow-none", "[&>input:focus]:ring-0"]);
  assert.doesNotMatch(ruleBody(css, ".\\[\\&\\>input\\:focus\\]\\:shadow-none") ?? "", /--tw-ring-shadow:/);
  assert.match(ruleBody(css, ".\\[\\&\\>input\\:focus\\]\\:ring-0") ?? "", /--tw-ring-shadow: [^;]*0 0 0 calc\(0px/);
});

test("under each variant the danger ring follows the ring, so an invalid control that carries both shows the danger ring", async () => {
  const css = await compiledCss(VARIANTS.flatMap(([variant]) => RINGS.map(([ring]) => `${variant}:${ring}`)));
  for (const [variant, pseudo] of VARIANTS) {
    const ring = css.indexOf(`.${variant}\\:focus-ring${pseudo}`);
    const danger = css.indexOf(`.${variant}\\:focus-ring-danger${pseudo}`);
    assert.ok(ring >= 0 && ring < danger, variant);
  }
});

// The composed box-shadow reads five variables; unregistered, an unset one voids the whole list.
const REGISTERED = ["--tw-ring-shadow", "--tw-shadow", "--tw-inset-shadow", "--tw-inset-ring-shadow", "--tw-ring-offset-shadow"];

/** The `initial-value` of each `@property` rule in compiled CSS. */
function registrations(css) {
  return new Map([...css.matchAll(/@property (--[\w-]+) \{([^}]*)\}/g)].map(([, name, body]) => [name, body.match(/initial-value: ([^;]+);/)?.[1].trim()]));
}

test("the ring registers the variables it composes, so an element with no shadow of its own still draws it", async () => {
  const initial = registrations(await compiledCss(["focus-visible:focus-ring"]));
  for (const name of REGISTERED) assert.equal(initial.get(name), "0 0 #0000", `${name} is registered with no shadow as its start`);
});

/** Split a CSS value at its top-level commas (or whitespace), keeping function arguments whole. */
function topLevel(value, separator) {
  const parts = [""];
  let depth = 0;
  for (const character of value) {
    if (character === "(") depth += 1;
    if (character === ")") depth -= 1;
    if (depth === 0 && separator.test(character)) parts.push(""); else parts[parts.length - 1] += character;
  }
  return parts.map(part => part.trim()).filter(Boolean);
}

/** Substitute `var()` as a browser does at computed time: an unset variable without a fallback voids the value. */
function substitute(value, variables) {
  const at = value.indexOf("var(");
  if (at < 0) return value;
  let depth = 0, end = at + 3;
  for (; end < value.length; end += 1) {
    if (value[end] === "(") depth += 1;
    if (value[end] === ")" && (depth -= 1) === 0) break;
  }
  const inner = value.slice(at + 4, end);
  const comma = topLevel(inner, /,/).length > 1 ? inner.indexOf(",") : -1;
  const name = (comma < 0 ? inner : inner.slice(0, comma)).trim();
  const replacement = variables.has(name) ? substitute(variables.get(name), variables) : comma < 0 ? null : substitute(inner.slice(comma + 1), variables);
  if (replacement === null) return null;
  const rest = substitute(value.slice(end + 1), variables);
  return rest === null ? null : `${value.slice(0, at)}${replacement}${rest}`;
}

/** The custom properties of every top-level rule of compiled CSS with exactly this selector, in order. */
function rootTokens(css, selector) {
  const tokens = new Map();
  for (let at = css.indexOf(`\n${selector} {`); at >= 0; at = css.indexOf(`\n${selector} {`, at + 1)) {
    for (const [, name, value] of ruleBody(css.slice(at), `${selector} {`).matchAll(/(--[\w-]+): ([^;]+);/g)) tokens.set(name, value.trim());
  }
  return tokens;
}

/** The shipped theme packages: every addon web package that exports `./themes`. */
function themePackages() {
  return readdirSync(join(root, "src/angee"), { withFileTypes: true }).filter(entry => entry.isDirectory())
    .map(entry => join(root, "src/angee", entry.name, "web/package.json"))
    .filter(file => existsSync(file) && JSON.parse(readFileSync(file, "utf8")).exports?.["./themes"])
    .map(file => JSON.parse(readFileSync(file, "utf8")).name).sort();
}

// The matrix judges a composed box-shadow with the theme contract's own judge. Rows on which that
// judge and Chromium agree (the ring drawn beside the last layer, or voided by it) keep it honest.
const judge = isShadowList;
const RING_LAYERS = "0 0 #0000, 0 0 #0000, 0 0 #0000, 0 0 0 3px rgb(37 99 235 / 30%)";
const MEASURED = [
  ["0 0 1PX #f00", true], ["rgb(0 0 0) 0 1px", true], ["0 1px 2px rgb(from #11141a r g b / 0.06)", true],
  ["1 1 #0000", false], ["0 0 -2px #0000", false], ["0 red 0", false], ["0 0 #0000 inset inset", false], ["0", false],
  ["transparent", false], ["none", false], ["0 0 #0000,", false], ["0 1px 2px rgb(0 0 0 0.1)", false], ["0 0 calc(1px)), none)", false],
];

test("every shipped theme, and each elevation a person can choose, keeps the ring beside each elevation utility", async () => {
  for (const [last, drawn] of MEASURED) assert.equal(judge(`${RING_LAYERS}, ${last}`), drawn, `the judge on a ring beside ${last}, as Chromium answers it`);
  const elevations = ["shadow-xs", "shadow-sm", "shadow-md", "shadow-lg", "shadow-popover"];
  const css = await compiledCss(["focus-visible:focus-ring", ...elevations]);
  const ringRule = new Map([...ruleBody(css, ".focus-visible\\:focus-ring").replace(/@[^{]+\{[^{}]*\}/g, "").matchAll(/([\w-]+): ([^;]+);/g)].map(([, name, value]) => [name, value.trim()]));
  const own = Object.fromEntries(elevations.map(utility => [utility, ruleBody(css, `.${utility} {`).match(/--tw-shadow: ([^;]+);/)[1]]));
  const base = { light: rootTokens(css, ":root") };
  base.dark = new Map([...base.light, ...rootTokens(css, '[data-color-scheme="dark"]')]);
  const packages = themePackages();
  assert.ok(packages.length >= 8, `found the shipped theme packages: ${packages.join(", ")}`);
  const presets = new Set();
  let checked = 0;
  for (const pkg of packages) {
    for (const theme of (await import(`${pkg}/themes`)).themes) {
      const choices = [["the theme's own elevation", theme.tokens]];
      if (isThemeCustomizationOptions(theme.options)) {
        for (const elevation of THEME_ELEVATION_PRESETS) {
          presets.add(elevation);
          choices.push([`elevation ${elevation}`, resolveThemeOptions(theme, { version: theme.options.version, value: { ...theme.options.defaults, elevation } }).tokens]);
        }
      }
      for (const [choice, layers] of choices) for (const scheme of ["light", "dark"]) {
        const document = new Map([...base[scheme], ...Object.entries(layers.shared), ...Object.entries(layers[scheme])]);
        const ring = substitute(document.get("--ring"), document);
        for (const utility of [null, ...elevations]) {
          const element = new Map([...document, ...[...registrations(css)].map(([name, value]) => [name, value]),
            ...(utility ? [["--tw-shadow", own[utility]]] : []), ["--tw-ring-shadow", ringRule.get("--tw-ring-shadow")]]);
          const boxShadow = substitute(ringRule.get("box-shadow"), element);
          const where = `${theme.id}, ${choice}, ${scheme}, ${utility ?? "no shadow utility"}: box-shadow ${boxShadow}`;
          assert.ok(boxShadow !== null && judge(boxShadow), `${where} is outside the contract's shadow bounds`);
          assert.ok(topLevel(boxShadow, /,/).includes(ring), `${where} lost the ring ${ring}`);
          checked += 1;
        }
      }
    }
  }
  assert.deepEqual([...presets].sort(), [...THEME_ELEVATION_PRESETS].sort(), "every elevation preset of the theme runtime is in the matrix");
  assert.ok(checked > 0);
});

test("no shipped theme stylesheet sets an elevation, which the composer refuses", async () => {
  let checked = 0;
  for (const pkg of themePackages()) {
    const entry = fileURLToPath(import.meta.resolve(`${pkg}/themes`));
    for (const theme of (await import(`${pkg}/themes`)).themes) for (const stylesheet of theme.stylesheets ?? []) {
      const file = join(dirname(entry), stylesheet);
      assertThemeStylesheet(readFileSync(file, "utf8"), file, theme.id);
      checked += 1;
    }
  }
  assert.ok(checked > 0, "a shipped theme has a stylesheet to check");
});

test("guard: the compile reads the real stylesheet (a registered utility of it composes with a variant)", async () => {
  const css = await compiledCss(["data-[pressed]:selected"]);
  assert.match(ruleBody(css, ".data-\\[pressed\\]\\:selected") ?? "", /background-color: var\(--brand-soft\)/);
});
