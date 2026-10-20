import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { before, test } from "node:test";
import ts from "typescript";
import {
  canonical, catalogSources, compileUtilities, generateCatalog,
  generateTokenNames, root, tokenModule, unwrap, walk,
} from "./catalog.mjs";
import { noOwner, unresolvedClasses } from "../src/stories/design-system/token-findings.ts";
import { storybookValueLookup } from "../src/url-values.ts";
import { COMPONENT_KINDS, parseCatalog, parseCompositionReport } from "../../ui/src/catalog-schema.ts";

const read = file => readFileSync(join(root, file), "utf8");
const catalog = parseCatalog(JSON.parse(read("packages/ui/src/catalog-data.json")));
const id = entry => `${entry.package}:${entry.name}`;
let sources, generated;
before(() => {
  sources = catalogSources();
  generated = generateCatalog(sources);
});

test("every catalog fact regenerates from declarations and CSF owners", () => {
  assert.equal(read("packages/ui/src/catalog-data.json"), canonical(generated.entries));
  assert.deepEqual(catalog.map(id), sources.exports.map(id).sort());
  assert(!sources.exports.some(entry => /Axes$/.test(entry.name) && entry.name !== "recipeAxes"));
  assert(!sources.exports.some(entry => entry.name === "componentCatalog"));
});

test("missing annotations, invalid places, owner cycles and non-value tokens fail", () => {
  const withTags = tags => ({ ...sources, exports: sources.exports.map(entry => entry.name === "Button" ? { ...entry, tags } : entry) });
  for (const [tags, error] of [
    [{}, /missing or invalid/], [{ kind: "null" }, /needs @reason/],
    [{ kind: "region", region: "Button" }, /invalid @region/],
    [{ owner: "MissingOwner" }, /unknown @owner/], [{ owner: "Button" }, /owner cycle/],
    [{ kind: "token" }, /value or recipe/],
  ]) assert.throws(() => generateCatalog(withTags(tags)), error);
  assert.throws(() => generateCatalog({
    ...sources, exports: sources.exports.map(entry => entry.name === "useAuthoredResourceMutation" ? { ...entry, tags: { kind: "surface" } } : entry),
  }), /classified hook needs @owner/);
});

test("ontology, upstream attribution, deprecations and owner inheritance remain truthful", () => {
  assert.deepEqual([...COMPONENT_KINDS].sort(), ["contribution", "fragment", "page", "primitive", "region", "surface", "token"]);
  for (const entry of catalog) {
    if (entry.owner) {
      const owner = catalog.find(candidate => id(candidate) === entry.owner);
      assert(owner, entry.owner);
      assert.deepEqual([entry.kind, entry.region, entry.reason], [owner.kind, owner.region, owner.reason]);
    }
    if (entry.storyId) assert(generated.stories.find(story => story.id === entry.storyId)?.owners.includes(id(entry)), id(entry));
  }
  for (const name of ["CARD_BASE", "STATUS_TONES"]) assert.equal(catalog.find(entry => entry.name === name)?.kind, "token", name);
  for (const name of ["useFormState", "useWatch", "useRender"]) {
    const entry = catalog.find(entry => entry.name === name);
    assert.equal(entry?.kind, null);
    assert.equal(entry?.reason, "upstream re-export");
    assert.match(entry.source, /^packages\/ui\/src\//);
  }
  assert.deepEqual(catalog.filter(entry => entry.status === "deprecated").map(entry => entry.name).sort(),
    ["DebouncedSearchInput", "Divider", "Tag", "TextInput", "TimelineEntry", "TimelineView"]);
  assert(catalog.some(entry => entry.kind !== null && entry.storyId === null));
  assert.throws(() => parseCatalog([{ ...catalog[0], role: "component" }]));
  assert.deepEqual(generated.regionDuplications["record-header"]?.map(owner => owner.name),
    ["FormViewRecordHeader", "PageHeader", "RecordHeader", "SurfaceHeader"]);
  assert(generated.stories.some(story => story.id === "fragments-emptystate--fill"));
  assert(!generated.stories.some(story => story.id === "fragments-emptystate--fill-centered-in-parent"));
});

test("guideline links name existing CSF files and native story ids", () => {
  const guideline = read("docs/frontend/guidelines.md");
  assert(!guideline.includes("127.0.0.1:7106"));
  const links = [...guideline.matchAll(/\]\(\.\.\/\.\.\/([^)]+)\)\s*\(story id `([^`]+)`\)/g)];
  assert(links.length >= 14);
  for (const [, storyId] of guideline.matchAll(/story id `([^`]+)`/g)) assert(generated.stories.some(story => story.id === storyId), storyId);
  for (const [, file, storyId] of links) assert.equal(generated.stories.find(story => story.id === storyId)?.file, file, storyId);
});

test("every specimen key is a classified export reachable by the Library resolver", () => {
  const source = sources.program.getSourceFile(join(root, "packages/storybook/src/stories/design-system/specimens.tsx"));
  assert(source, "Missing specimen module");
  const declaration = source.statements.flatMap(statement => ts.isVariableStatement(statement) ? [...statement.declarationList.declarations] : [])
    .find(declaration => declaration.name.getText(source) === "specimens");
  const registry = unwrap(declaration?.initializer);
  assert(registry && ts.isObjectLiteralExpression(registry), "Missing specimen registry");
  for (const property of registry.properties) {
    const name = property.name?.getText(source);
    assert(catalog.find(entry => entry.name === name)?.kind, name);
  }
});

test("addon route instances are informational and graph kinds follow the catalog", () => {
  const report = parseCompositionReport(JSON.parse(read("packages/storybook/reports/composition.json")));
  for (const node of report.nodes) {
    if (node.routeInstance || ["token", "lib", "route", "route-alias"].includes(node.prefix)) assert.equal(node.ownershipViolation, false, node.id);
    if (node.prefix === "route" || node.prefix === "route-alias") assert.equal(node.kind, null, node.id);
    const entry = catalog.find(entry => `${entry.package.slice(7)}:${entry.name}` === node.id);
    if (entry) assert.equal(node.kind, entry.kind, node.id);
  }
  assert.equal(report.nodes.find(node => node.id === "agents:AgentSessionsPage")?.routeInstance, true);
  assert.equal(report.nodes.find(node => node.id === "dashboards:DashboardPage")?.routeInstance, true);
  assert(!report.nodes.some(node => node.ownershipViolation && node.kind === "page"));
});

test("URL values distinguish dots, underscores, hyphens and graph id punctuation", () => {
  const ids = ["a.b", "a-b", "a_b", "ui:FormView", "a_2Eb", "a!b"];
  const lookup = storybookValueLookup(ids);
  assert.equal(lookup.size, ids.length);
  assert.deepEqual([...lookup.values()], ids);
  for (const value of lookup.keys()) assert.match(value, /^[a-zA-Z0-9 _-]*$/);
});

test("the public token-name list follows the stylesheet export", () => {
  assert.equal(read("packages/ui/src/token-names.ts"), tokenModule(generateTokenNames()));
});

test("no-owner tokens exist and have no production variable or utility reference", async () => {
  const production = ["packages/ui/src", "addons"].flatMap(directory => walk(join(root, directory)))
    .filter(file => /\.(?:css|[cm]?[jt]sx?)$/.test(file) && !/\.(?:test|stories)\./.test(file) && !file.endsWith("token-names.ts"))
    .map(file => readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "")).join("\n");
  const compiler = await compileUtilities();
  const compiled = compiler.build([...new Set(production.match(/[a-zA-Z0-9_!@#[\].:/%()-]+/g) ?? [])]);
  const names = new Set(generateTokenNames());
  for (const token of noOwner) {
    assert(names.has(token), `${token} must exist`);
    const reference = new RegExp(`var\\(\\s*${token}(?:\\s*[,\\)])`);
    assert(!reference.test(production) && !reference.test(compiled), `${token} has a production reference`);
  }
});

test("unresolved utility findings compile to no utility", async () => {
  const compiler = await compileUtilities();
  const baseline = compiler.build([]);
  assert.equal(compiler.build([...unresolvedClasses]), baseline);
});
