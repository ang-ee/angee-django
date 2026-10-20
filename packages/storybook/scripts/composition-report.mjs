import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { parseCatalog, parseCompositionReport } from "../../ui/src/catalog-schema.ts";

const file = fileURLToPath(new URL("../reports/composition.json", import.meta.url));
const graph = parseCompositionReport(JSON.parse(readFileSync(file, "utf8")));
const catalog = parseCatalog(JSON.parse(readFileSync(new URL("../../ui/src/catalog-data.json", import.meta.url), "utf8")));
const exported = new Map(catalog.map(entry => [`${entry.package.slice(7)}:${entry.name}`, entry]));
const pages = new Set(graph.pages);
// Inventory namespaces for framework owners and synthetic graph declarations.
const nonAddon = new Set(["ui", "app", "refine", "metadata", "token", "route", "route-alias", "storybook", "container", "registry", "slot", "lib"]);
const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const fail = message => { throw new Error(`Composition report: ${message}`); };
if (graph.version !== 0) fail("expected inventory version 0");
const ids = new Set();
for (const node of graph.nodes) {
  if (typeof node.id !== "string" || ids.has(node.id)) fail(`duplicate or invalid node ${node.id}`);
  ids.add(node.id);
  const entry = exported.get(node.id);
  if (entry) {
    node.kind = entry.kind;
    node.kindBasis = "catalog";
    if (entry.reason) node.reason = entry.reason;
    else delete node.reason;
  }
  if (node.prefix === "route" || node.prefix === "route-alias") {
    node.kind = null;
    node.kindBasis = "route declaration";
    node.reason = "Runtime routing declaration; the target owns the page composition.";
  }
  // Inventory page classifications include wrappers reached through another
  // routed component; they await page shapes just like direct route targets.
  node.routeInstance = !nonAddon.has(node.prefix) && (pages.has(node.id) || node.kind === "page");
  node.ownershipViolation = node.shipped && !node.routeInstance && !nonAddon.has(node.prefix)
    && ["token", "primitive", "fragment", "region", "page"].includes(node.kind);
  if (node.kind === null && !node.reason) fail(`unclassified node ${node.id} needs a reason`);
}
for (const edge of graph.edges) {
  if (!ids.has(edge.from) || !ids.has(edge.to)) fail(`dangling edge ${edge.from} → ${edge.to}`);
}
for (const key of ["pages", "hand_rolled"]) {
  if (!Array.isArray(graph[key]) || graph[key].some(id => !ids.has(id))) fail(`invalid ${key}`);
}
for (const [name, nodes] of Object.entries(graph.duplicates)) {
  if (!Array.isArray(nodes) || nodes.some(id => !ids.has(id))) fail(`invalid duplicate ${name}`);
}
if (Object.entries(graph.fan_in).some(([id, count]) => !ids.has(id) || !Number.isInteger(count) || count < 0)) fail("invalid fan-in");
// Canonicalize object keys and every list. No clock or filesystem iteration order
// enters this snapshot. Lane 2 replaces only the source with a source-code scan.
function canonical(value) {
  if (Array.isArray(value)) {
    const items = value.map(canonical);
    return items.sort((a, b) => compare(
      typeof a === "string" ? a : a.id ?? `${a.from}\0${a.to}\0${a.file ?? ""}\0${a.source ?? ""}`,
      typeof b === "string" ? b : b.id ?? `${b.from}\0${b.to}\0${b.file ?? ""}\0${b.source ?? ""}`,
    ));
  }
  if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  return value;
}
const sorted = canonical(graph);
// Compact emission: one node, edge or map entry per line, so the snapshot diffs by
// entry instead of by indented field and stays a fraction of the pretty size.
function emit(value, indent = "") {
  if (Array.isArray(value)) {
    if (value.every(item => typeof item !== "object" || item === null)) return JSON.stringify(value);
    return `[\n${value.map(item => `${indent}  ${JSON.stringify(item)}`).join(",\n")}\n${indent}]`;
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value);
    if (entries.length === 0) return "{}";
    return `{\n${entries.map(([key, item]) => `${indent}  ${JSON.stringify(key)}: ${emit(item, `${indent}  `)}`).join(",\n")}\n${indent}}`;
  }
  return JSON.stringify(value);
}
const output = `${emit(sorted)}\n`;
if (process.argv.includes("--check")) {
  if (readFileSync(file, "utf8") !== output) fail("run composition-report.mjs to reconcile catalog kinds, route instances and snapshot ordering");
} else {
  writeFileSync(file, output);
}
console.log(`Composition v0: ${graph.nodes.length} nodes, ${graph.edges.length} edges; valid and sorted.`);
