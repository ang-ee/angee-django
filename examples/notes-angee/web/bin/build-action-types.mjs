#!/usr/bin/env node
// Derive the action-mutation allow-list from each schema's emitted SDL. An action
// field is a Mutation field with exactly one `id: ID!` argument that returns the
// backend-owned `ActionResult`; the emitted `ActionFieldName` union pins
// `useActionMutation` (@angee/sdk) to real action fields at compile time. Written
// beside the client-preset output so `@angee/gql/<schema>/actions` resolves via
// the same alias. Generated from the one source of truth (the SDL).

import { readFileSync, writeFileSync } from "node:fs";

import { GraphQLObjectType, buildSchema, getNamedType } from "graphql";

const SCHEMAS = ["public", "console"];

function actionFields(sdlPath) {
  const schema = buildSchema(readFileSync(sdlPath, "utf8"));
  const mutation = schema.getMutationType();
  if (!mutation) return [];
  const fields = mutation.getFields();
  return Object.keys(fields)
    .filter((name) => {
      const field = fields[name];
      if (field.args.length !== 1) return false;
      const arg = field.args[0];
      if (arg.name !== "id" || String(arg.type) !== "ID!") return false;
      const returned = getNamedType(field.type);
      return returned instanceof GraphQLObjectType && returned.name === "ActionResult";
    })
    .sort();
}

for (const name of SCHEMAS) {
  const sdlPath = new URL(`../../runtime/schemas/${name}.graphql`, import.meta.url);
  const outPath = new URL(`../../runtime/gql/${name}/actions.ts`, import.meta.url);
  const names = actionFields(sdlPath);
  const union = names.length > 0 ? names.map((n) => `"${n}"`).join(" | ") : "never";
  const body = [
    `// Generated from runtime/schemas/${name}.graphql — do not edit by hand.`,
    "// Run `pnpm codegen` to regenerate.",
    "//",
    "// Mutation fields shaped `<field>(id: ID!): ActionResult` — the",
    "// compile-time allow-list for `useActionMutation` (@angee/sdk).",
    "",
    `export type ActionFieldName = ${union};`,
    "",
  ].join("\n");
  writeFileSync(outPath, body);
  console.log(`actions [${name}]: ${names.length} field(s)`);
}
