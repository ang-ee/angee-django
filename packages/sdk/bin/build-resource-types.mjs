#!/usr/bin/env node
// Derive the resource type map from the pinned contract. A model is
// "resource-shaped" when the schema declares both `<Type>Filter` and
// `<Type>Order` inputs for it; the emitted map lets the resource hooks pin
// `filter` / `order` to a model name. Generated from the one source of truth
// (the SDL) so it cannot drift from the types codegen emits.

import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { buildSchema } from "graphql";

const here = fileURLToPath(new URL(".", import.meta.url));
const schemaPath = new URL("../schema/contract.graphql", import.meta.url);
const outPath = new URL("../src/__generated__/resource-types.ts", import.meta.url);

const schema = buildSchema(readFileSync(schemaPath, "utf8"));
const typeMap = schema.getTypeMap();

const models = Object.keys(typeMap)
  .filter((name) => typeMap[`${name}Filter`] && typeMap[`${name}Order`])
  .sort();

const imports = models.flatMap((name) => [`${name}Filter`, `${name}Order`]);

const lines = [
  "// Generated from schema/contract.graphql — do not edit by hand.",
  "// Run `pnpm codegen` to regenerate.",
  "",
  `import type {\n${imports.map((name) => `  ${name},`).join("\n")}\n} from "./public";`,
  "",
  "export interface ResourceTypeMap {",
  ...models.map(
    (name) => `  ${name}: { Filter: ${name}Filter; Order: ${name}Order };`,
  ),
  "}",
  "",
  "export type ResourceTypeName = keyof ResourceTypeMap;",
  "export type ResourceFilter<TName extends ResourceTypeName> =",
  '  ResourceTypeMap[TName]["Filter"];',
  "export type ResourceOrder<TName extends ResourceTypeName> =",
  '  ResourceTypeMap[TName]["Order"];',
  "",
];

writeFileSync(outPath, lines.join("\n"));
process.stdout.write(`resource-types: ${models.length} model(s) [${models.join(", ")}]\n`);
void here;
