import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { compile } from "tailwindcss";

const entry = fileURLToPath(new URL("../src/storybook.css", import.meta.url));

/**
 * Compile the workshop's stylesheet entry, the same one a host imports (`tailwindcss`, then
 * `@angee/ui/styles.css`), for the given class candidates. Tailwind's `compile` asks its caller
 * to load each `@import`: a relative one from its file, a `.css` subpath through the package's
 * exports, `tailwindcss` from its stylesheet. Any other bare package (the animation presets)
 * exposes only a `style` condition Node does not resolve, and holds nothing these tests read.
 */
async function compiledCss(candidates) {
  const loadStylesheet = async (id, base) => {
    const require = createRequire(path.join(base, "index.js"));
    const file = id.startsWith(".") ? path.resolve(base, id)
      : id === "tailwindcss" ? require.resolve("tailwindcss/index.css")
      : id.endsWith(".css") ? require.resolve(id)
      : null;
    if (file === null) return { path: path.join(base, `${id}.css`), base, content: "" };
    return { path: file, base: path.dirname(file), content: await readFile(file, "utf8") };
  };
  const compiler = await compile(await readFile(entry, "utf8"), { base: path.dirname(entry), from: entry, loadStylesheet });
  return compiler.build(candidates);
}

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
    assert.match(ruleBody(css, `.${ring} {`) ?? ruleBody(css, `.${ring}{`) ?? "", new RegExp(`box-shadow: ${shadow.replace(/[()]/g, "\\$&")}`));
  });

  for (const [variant, pseudo] of VARIANTS) {
    test(`${variant}:${ring} compiles to a rule that draws the ring on ${pseudo}`, async () => {
      const css = await compiledCss([`${variant}:${ring}`]);
      const body = ruleBody(css, `.${variant}\\:${ring}`);
      assert.notEqual(body, null, `no rule for ${variant}:${ring} in the compiled stylesheet`);
      assert.match(body, new RegExp(`box-shadow: ${shadow.replace(/[()]/g, "\\$&")}`));
      assert.ok(body.includes(pseudo) || css.includes(`.${variant}\\:${ring}${pseudo}`), `the rule is not scoped to ${pseudo}`);
    });
  }
}

// The two other shapes the packages spell: a ring while a control's popup is open, and the danger ring
// of an invalid control that has focus.
for (const [candidate, selector, shadow] of [
  ["data-[popup-open]:focus-ring", ".data-\\[popup-open\\]\\:focus-ring", "var(--ring)"],
  ["data-[invalid]:focus:focus-ring-danger", ".data-\\[invalid\\]\\:focus\\:focus-ring-danger", "var(--ring-danger)"],
]) {
  test(`${candidate} compiles to a rule that draws the ring`, async () => {
    const body = ruleBody(await compiledCss([candidate]), selector);
    assert.notEqual(body, null, `no rule for ${candidate} in the compiled stylesheet`);
    assert.match(body, new RegExp(`box-shadow: ${shadow.replace(/[()]/g, "\\$&")}`));
  });
}

test("guard: the compile reads the real stylesheet (a registered utility of it composes with a variant)", async () => {
  const css = await compiledCss(["data-[pressed]:selected"]);
  assert.match(ruleBody(css, ".data-\\[pressed\\]\\:selected") ?? "", /background-color: var\(--brand-soft\)/);
});
