#!/usr/bin/env node

import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";

import { generate } from "@graphql-codegen/cli";
import {
  GraphQLObjectType,
  buildSchema,
  getNamedType,
  parse,
} from "graphql";

const AGGREGATE_MEASURE_OPERATORS = ["sum", "avg", "min", "max"];
const DELETE_PREVIEW_SELECTION =
  "total_deleted_count has_blockers " +
  "deleted { label count } updated { label count } blocked { label count } " +
  "root { label object_label object_id " +
  "children { label object_label object_id " +
  "children { label object_label object_id } } }";
const SCALARS = {
  DateTime: "string",
  Date: "string",
  BigInt: "string",
  Decimal: "string",
  JSON: "unknown",
};
const ADDON_ENTRY_EXTENSIONS = [".ts", ".tsx", ".js", ".jsx"];

const options = parseOptions(process.argv.slice(2));
const webRoot = resolveFromCwd(options["web-root"] ?? ".");
const runtimeDir = resolveFromCwd(options.runtime ?? "../runtime");
const manifest = readManifest(runtimeDir);
const addonPackages = addonPackagesFor(manifest);
preflightAddonPackages(webRoot, addonPackages);
const externalEntries = Array.isArray(manifest.codegen) ? manifest.codegen : [];
const djangoSchemas = schemaNamesFor(runtimeDir);
const documentRoots = documentRootsFor(webRoot, manifest);

// Django Angee schemas: client preset + authored operation documents, composed
// as createApp schemas. Their SDL is the GraphQLSdl-owned runtime/schemas tree.
for (const name of djangoSchemas) {
  const schemaPath = path.join(runtimeDir, "schemas", `${name}.graphql`);
  await runCodegen(name, schemaPath, runtimeDir, documentGlobs(name, documentRoots), false);
  buildOperationDocuments(name, runtimeDir);
}
// External schemas (the operator daemon): the addon owns the committed SDL, read
// straight from node_modules. No Angee resource metadata, not a createApp schema.
for (const entry of externalEntries) {
  const schemaPath = path.resolve(webRoot, "node_modules", entry.package, entry.sdl);
  const documents = documentRoots.map((root) => `${root}/**/${entry.documents}`);
  await runCodegen(entry.schema, schemaPath, runtimeDir, documents, entry.types === true);
}
emitAppModule(runtimeDir, webRoot, addonPackages, djangoSchemas);

function parseOptions(args) {
  const parsed = {};
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (!arg.startsWith("--")) {
      throw new Error(`Unexpected argument: ${arg}`);
    }
    const key = arg.slice(2);
    const value = args[index + 1];
    if (!value || value.startsWith("--")) {
      throw new Error(`Missing value for --${key}`);
    }
    parsed[key] = value;
    index += 1;
  }
  return parsed;
}

function resolveFromCwd(value) {
  return path.isAbsolute(value) ? value : path.resolve(process.cwd(), value);
}

function readManifest(runtimeDir) {
  const manifestPath = path.join(runtimeDir, "web", "manifest.json");
  if (!existsSync(manifestPath)) {
    throw new Error(`Missing frontend runtime manifest: ${manifestPath}`);
  }
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  if (manifest?.schema !== 1) {
    throw new Error(`Unsupported frontend runtime manifest schema in ${manifestPath}`);
  }
  return manifest;
}

function addonPackagesFor(manifest) {
  return Array.isArray(manifest.addonPackages) ? manifest.addonPackages : [];
}

function preflightAddonPackages(webRoot, addonPackages) {
  if (addonPackages.length === 0) return;
  const packageJsonPath = path.join(webRoot, "package.json");
  if (!existsSync(packageJsonPath)) {
    throw new Error(`Missing host web package manifest: ${packageJsonPath}`);
  }
  const hostPackage = JSON.parse(readFileSync(packageJsonPath, "utf8"));
  const dependencies =
    hostPackage?.dependencies &&
    typeof hostPackage.dependencies === "object" &&
    !Array.isArray(hostPackage.dependencies)
      ? hostPackage.dependencies
      : {};
  for (const pkg of addonPackages) {
    if (!pkg || typeof pkg.package !== "string" || pkg.package.length === 0) {
      continue;
    }
    if (!Object.hasOwn(dependencies, pkg.package)) {
      throw new Error(
        `${addonNameFor(pkg)} declares frontend package ${pkg.package}, ` +
          `but it is missing from ${packageJsonPath}`,
      );
    }
  }
  for (const pkg of addonPackages) {
    const entryBase = addonEntryBase(webRoot, pkg);
    if (!addonEntryExtension(entryBase)) {
      throw new Error(
        `${addonNameFor(pkg)} declares frontend package ${pkg.package}, ` +
          `but it cannot be resolved from ${path.join(webRoot, "node_modules", pkg.package)}. ` +
          `Expected ${entryBase}.{ts,tsx,js,jsx}; run pnpm install.`,
      );
    }
  }
}

function schemaNamesFor(runtimeDir) {
  // The SDL on disk is the source of truth for which schemas exist: the Django
  // `schema` command emits `runtime/schemas/<name>.graphql`, and external owners
  // (the operator daemon) deposit their SDL into the same directory.
  const schemaDir = path.join(runtimeDir, "schemas");
  if (!existsSync(schemaDir)) return [];
  return readdirSync(schemaDir)
    .filter((name) => name.endsWith(".graphql"))
    .map((name) => name.slice(0, -".graphql".length))
    .sort();
}

function documentRootsFor(webRoot, manifest) {
  const roots = Array.isArray(manifest.documentRoots) ? manifest.documentRoots : [];
  return roots
    .flatMap((entry) => {
      if (!entry || typeof entry.path !== "string" || entry.path.length === 0) {
        return [];
      }
      return [slash(path.resolve(webRoot, entry.path))];
    })
    .sort();
}

function schemaIsLive(sdlPath) {
  // A schema is live when its SDL declares a Subscription root — the schema's
  // own contract, not a guess from its name.
  return buildSchema(readFileSync(sdlPath, "utf8")).getSubscriptionType() != null;
}

function emitAppModule(runtimeDir, webRoot, addonPackages, schemaNames) {
  // `runtime/web/app.ts` lives outside the web package's module-resolution
  // scope, so addon packages are imported by their on-disk entry under the web
  // package's node_modules. The path is derived from the real --web-root (not a
  // fixed constant), so a project that relocates its web package still resolves.
  const webRel = slash(path.relative(path.join(runtimeDir, "web"), webRoot));
  const addonImports = addonPackages.map((pkg, index) => {
    const entry = addonEntryImport(webRoot, webRel, pkg);
    return `import addon${index} from ${JSON.stringify(entry)};`;
  });
  const schemaImports = [];
  const schemaEntries = schemaNames.map((name, index) => {
    schemaImports.push(
      `import schema${index}Metadata from ${JSON.stringify(`../schemas/${name}.metadata.json`)};`,
      `import { operationDocuments as schema${index}Documents } from ${JSON.stringify(`../gql/${name}/actions`)};`,
    );
    const sdlPath = path.join(runtimeDir, "schemas", `${name}.graphql`);
    const lines = [
      `  ${JSON.stringify(name)}: {`,
      `    url: "/graphql/${name}/",`,
      `    metadata: schema${index}Metadata,`,
      `    operationDocuments: schema${index}Documents,`,
    ];
    if (schemaIsLive(sdlPath)) lines.push("    live: true,");
    lines.push("  },");
    return lines.join("\n");
  });
  const addonValues = addonPackages.map((_pkg, index) => `addon${index}`).join(", ");
  const body = [
    "// Generated composed web runtime - do not edit by hand.",
    "// Run `pnpm codegen`; `manage.py angee build` emits the manifest it reads.",
    "",
    [...addonImports, ...schemaImports].join("\n"),
    "",
    `export const composedAddons = [${addonValues}] as const;`,
    "",
    "export const schemas = {",
    schemaEntries.join("\n"),
    "} as const;",
    "",
  ].join("\n");
  const outPath = path.join(runtimeDir, "web", "app.ts");
  mkdirSync(path.dirname(outPath), { recursive: true });
  writeFileSync(outPath, body);
  console.log(
    `composed web runtime: ${addonPackages.length} addon(s), ${schemaNames.length} schema(s)`,
  );
}

function addonEntryImport(webRoot, webRel, pkg) {
  const entryBase = addonEntryBase(webRoot, pkg);
  const extension = addonEntryExtension(entryBase);
  if (!extension) {
    throw new Error(
      `${addonNameFor(pkg)} declares frontend package ${pkg.package}, ` +
        `but it cannot be resolved from ${path.join(webRoot, "node_modules", pkg.package)}. ` +
        `Expected ${entryBase}.{ts,tsx,js,jsx}; run pnpm install.`,
    );
  }
  return `${webRel}/node_modules/${pkg.package}/${sourceRootFor(pkg)}/index${extension}`;
}

function addonEntryBase(webRoot, pkg) {
  return path.join(webRoot, "node_modules", pkg.package, sourceRootFor(pkg), "index");
}

function addonEntryExtension(entryBase) {
  return ADDON_ENTRY_EXTENSIONS.find((candidate) => existsSync(`${entryBase}${candidate}`));
}

function sourceRootFor(pkg) {
  const sourceRoot = typeof pkg.sourceRoot === "string" ? pkg.sourceRoot : "src";
  return sourceRoot;
}

function addonNameFor(pkg) {
  return typeof pkg.app === "string" && pkg.app.length > 0 ? pkg.app : "An enabled addon";
}

async function runCodegen(name, schemaPath, runtimeDir, documents, types) {
  if (!existsSync(schemaPath)) {
    throw new Error(`Missing GraphQL SDL for schema ${name}: ${schemaPath}`);
  }
  const codegenConfig = {
    scalars: SCALARS,
    enumsAsTypes: true,
    skipTypename: true,
    useTypeImports: true,
  };
  const generates = {
    [slash(path.join(runtimeDir, "gql", name, path.sep))]: {
      preset: "client",
      presetConfig: { fragmentMasking: false },
      config: codegenConfig,
    },
  };
  // An external schema may also need a bare `typescript` types module (the
  // operator console re-exports named daemon types, which the client preset
  // does not surface as standalone exports).
  if (types) {
    generates[slash(path.join(runtimeDir, "gql", name, "types.ts"))] = {
      plugins: ["typescript"],
      config: codegenConfig,
    };
  }
  await generate(
    { schema: slash(schemaPath), documents, ignoreNoDocuments: true, generates },
    true,
  );
}

function documentGlobs(name, roots) {
  const files =
    name === "public"
      ? ["documents.public.ts"]
      : ["documents.ts", `documents.${name}.ts`];
  return roots.flatMap((root) => files.map((file) => `${root}/**/${file}`));
}

function buildOperationDocuments(name, runtimeDir) {
  const sdlPath = path.join(runtimeDir, "schemas", `${name}.graphql`);
  const metadataPath = path.join(runtimeDir, "schemas", `${name}.metadata.json`);
  const outPath = path.join(runtimeDir, "gql", name, "actions.ts");
  const names = actionFields(sdlPath);
  const aggregateResources = aggregateFields(metadataPath);
  const deletePreviewResources = deletePreviewFields(metadataPath);
  const groupResources = groupFields(metadataPath);
  const revisionResources = revisionFields(metadataPath);
  const saveResources = saveFields(metadataPath);
  const union = names.length > 0 ? names.map((n) => JSON.stringify(n)).join(" | ") : "never";
  const aggregateUnion = aggregateResources.length > 0
    ? aggregateResources.map((resource) => JSON.stringify(resource.modelLabel)).join(" | ")
    : "never";
  const deletePreviewUnion = deletePreviewResources.length > 0
    ? deletePreviewResources.map((resource) => JSON.stringify(resource.modelLabel)).join(" | ")
    : "never";
  const groupUnion = groupResources.length > 0
    ? groupResources.map((resource) => JSON.stringify(resource.modelLabel)).join(" | ")
    : "never";
  const revisionUnion = revisionResources.length > 0
    ? revisionResources.map((resource) => JSON.stringify(resource.modelLabel)).join(" | ")
    : "never";
  const saveUnion = saveResources.length > 0
    ? saveResources.map((resource) => JSON.stringify(resource.modelLabel)).join(" | ")
    : "never";
  const documents = names.map((field) => {
    const ast = JSON.stringify(actionDocument(field), null, 2);
    return `  ${JSON.stringify(field)}: ${ast} as ActionDocument<${JSON.stringify(field)}>,`;
  });
  const aggregateDocuments = aggregateResources.map((resource) => {
    const ast = JSON.stringify(
      aggregateDocument(resource.aggregateRoot, resource.filterType, resource.measures),
      null,
      2,
    );
    return `  ${JSON.stringify(resource.modelLabel)}: ${ast} as AggregateDocument,`;
  });
  const deletePreviewDocuments = deletePreviewResources.map((resource) => {
    const ast = JSON.stringify(deletePreviewDocument(resource.deletePreviewRoot), null, 2);
    return `  ${JSON.stringify(resource.modelLabel)}: ${ast} as DeletePreviewDocument,`;
  });
  const groupDocuments = groupResources.map((resource) => {
    const ast = JSON.stringify(groupDocument(resource), null, 2);
    return `  ${JSON.stringify(resource.modelLabel)}: ${ast} as GroupDocument,`;
  });
  const revisionDocuments = revisionResources.map((resource) => {
    const ast = JSON.stringify(revisionDocument(resource.revisionsRoot, resource.fields), null, 2);
    return `  ${JSON.stringify(resource.modelLabel)}: ${ast} as RevisionDocument,`;
  });
  const saveDocuments = saveResources.map((resource) => {
    const ast = JSON.stringify(saveDocument(resource), null, 2);
    return `  ${JSON.stringify(resource.modelLabel)}: ${ast} as SaveDocument,`;
  });
  const body = [
    `// Generated from runtime/schemas/${name}.graphql - do not edit by hand.`,
    "// Run `pnpm codegen` to regenerate.",
    "//",
    "// Mutation fields shaped `<field>(id: ID!): ActionResult` plus authored",
    "// aggregate, group, delete-preview, and revision operation documents.",
    "",
    "import type { TypedDocumentNode } from \"@graphql-typed-document-node/core\";",
    "",
    "export interface ActionResult {",
    "  ok: boolean;",
    "  message: string;",
    "  id: string | null;",
    "  validation_errors: Record<string, string[]> | null;",
    "}",
    "",
    "export interface ActionVariables {",
    "  id: string;",
    "}",
    "",
    `export type ActionFieldName = ${union};`,
    "",
    "export type ActionDocument<",
    "  TField extends ActionFieldName = ActionFieldName,",
    "> = TypedDocumentNode<Record<TField, ActionResult>, ActionVariables>;",
    "",
    "export const actionDocuments: {",
    "  readonly [Field in ActionFieldName]: ActionDocument<Field>;",
    "} = {",
    ...documents,
    "};",
    "",
    `export type AggregateResource = ${aggregateUnion};`,
    "",
    "export interface AggregateVariables {",
    "  where?: Record<string, unknown>;",
    "}",
    "",
    "export type AggregateDocument = TypedDocumentNode<",
    "  Record<string, { aggregate: Record<string, unknown> }>,",
    "  AggregateVariables",
    ">;",
    "",
    "export const aggregateDocuments: {",
    "  readonly [Resource in AggregateResource]: AggregateDocument;",
    "} = {",
    ...aggregateDocuments,
    "};",
    "",
    `export type DeletePreviewResource = ${deletePreviewUnion};`,
    "",
    "export interface DeletePreviewVariables {",
    "  id: string;",
    "  confirm?: boolean;",
    "}",
    "",
    "export interface DeletePreviewGroup {",
    "  label: string;",
    "  count: number;",
    "}",
    "",
    "export interface DeletePreviewNode {",
    "  label: string;",
    "  object_label: string;",
    "  object_id: string | null;",
    "  children: DeletePreviewNode[];",
    "}",
    "",
    "export interface DeletePreview {",
    "  total_deleted_count: number;",
    "  has_blockers: boolean;",
    "  deleted: DeletePreviewGroup[];",
    "  updated: DeletePreviewGroup[];",
    "  blocked: DeletePreviewGroup[];",
    "  root: DeletePreviewNode;",
    "}",
    "",
    "export type DeletePreviewDocument = TypedDocumentNode<",
    "  Record<string, DeletePreview>,",
    "  DeletePreviewVariables",
    ">;",
    "",
    "export const deletePreviewDocuments: {",
    "  readonly [Resource in DeletePreviewResource]: DeletePreviewDocument;",
    "} = {",
    ...deletePreviewDocuments,
    "};",
    "",
    `export type GroupResource = ${groupUnion};`,
    "",
    "export interface GroupVariables {",
    "  group_by: readonly Record<string, unknown>[];",
    "  where?: Record<string, unknown>;",
    "  order_by?: readonly Record<string, unknown>[];",
    "  limit?: number;",
    "  offset?: number;",
    "}",
    "",
    "export type GroupDocument = TypedDocumentNode<",
    "  Record<string, { key: Record<string, unknown>; aggregate: Record<string, unknown> }[]>,",
    "  GroupVariables",
    ">;",
    "",
    "export const groupDocuments: {",
    "  readonly [Resource in GroupResource]: GroupDocument;",
    "} = {",
    ...groupDocuments,
    "};",
    "",
    `export type RevisionResource = ${revisionUnion};`,
    "",
    "export interface RevisionVariables {",
    "  id: string;",
    "}",
    "",
    "export type RevisionRecord = Record<string, unknown> & {",
    "  id: string;",
    "  created_at?: string;",
    "  comment?: string | null;",
    "};",
    "",
    "export type RevisionDocument = TypedDocumentNode<",
    "  Record<string, RevisionRecord[]>,",
    "  RevisionVariables",
    ">;",
    "",
    "export const revisionDocuments: {",
    "  readonly [Resource in RevisionResource]: RevisionDocument;",
    "} = {",
    ...revisionDocuments,
    "};",
    "",
    `export type SaveResource = ${saveUnion};`,
    "",
    "export interface SaveVariables {",
    "  pk: string;",
    "  patch?: Record<string, unknown>;",
    "  lines?: readonly Record<string, unknown>[];",
    "}",
    "",
    "export type SaveDocument = TypedDocumentNode<",
    "  Record<string, Record<string, unknown>>,",
    "  SaveVariables",
    ">;",
    "",
    "export const saveDocuments: {",
    "  readonly [Resource in SaveResource]: SaveDocument;",
    "} = {",
    ...saveDocuments,
    "};",
    "",
    "// One object shaped as @angee/refine's SchemaOperationDocuments — the single",
    "// symbol the composed web runtime imports per schema.",
    "export const operationDocuments = {",
    "  actions: actionDocuments,",
    "  aggregates: aggregateDocuments,",
    "  deletePreviews: deletePreviewDocuments,",
    "  groups: groupDocuments,",
    "  revisions: revisionDocuments,",
    "  saves: saveDocuments,",
    "};",
    "",
  ].join("\n");
  mkdirSync(path.dirname(outPath), { recursive: true });
  writeFileSync(outPath, body);
  console.log(
    `operation documents [${name}]: ` +
      `${names.length} action(s), ` +
      `${aggregateResources.length} aggregate query(ies), ` +
      `${deletePreviewResources.length} delete-preview mutation(s), ` +
      `${groupResources.length} group query(ies), ` +
      `${revisionResources.length} revision query(ies), ` +
      `${saveResources.length} save mutation(s)`,
  );
}

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

function deletePreviewFields(metadataPath) {
  const metadata = JSON.parse(readFileSync(metadataPath, "utf8"));
  const resources = metadata?.angee?.resources;
  if (!Array.isArray(resources)) return [];
  return resources
    .flatMap((resource) => {
      const modelLabel = resource?.modelLabel;
      const deletePreviewRoot = resource?.roots?.deletePreview;
      if (
        typeof modelLabel !== "string" ||
        typeof deletePreviewRoot !== "string" ||
        deletePreviewRoot === ""
      ) {
        return [];
      }
      return [{ modelLabel, deletePreviewRoot }];
    })
    .sort((left, right) => left.modelLabel.localeCompare(right.modelLabel));
}

function aggregateFields(metadataPath) {
  const metadata = JSON.parse(readFileSync(metadataPath, "utf8"));
  const resources = metadata?.angee?.resources;
  if (!Array.isArray(resources)) return [];
  return resources
    .flatMap((resource) => {
      const modelLabel = resource?.modelLabel;
      const aggregateRoot = resource?.roots?.aggregate;
      const filterType = resource?.typeNames?.filter;
      if (
        typeof modelLabel !== "string" ||
        typeof aggregateRoot !== "string" ||
        aggregateRoot === "" ||
        typeof filterType !== "string" ||
        filterType === ""
      ) {
        return [];
      }
      return [{
        modelLabel,
        aggregateRoot,
        filterType,
        measures: Array.isArray(resource?.aggregateMeasures)
          ? resource.aggregateMeasures
          : [],
      }];
    })
    .sort((left, right) => left.modelLabel.localeCompare(right.modelLabel));
}

function groupFields(metadataPath) {
  const metadata = JSON.parse(readFileSync(metadataPath, "utf8"));
  const resources = metadata?.angee?.resources;
  if (!Array.isArray(resources)) return [];
  return resources
    .flatMap((resource) => {
      const modelLabel = resource?.modelLabel;
      const groupsRoot = resource?.roots?.groups;
      const filterType = resource?.typeNames?.filter;
      const groupByType = resource?.typeNames?.groupBySpec;
      const groupOrderType = resource?.typeNames?.groupOrder;
      const keyFields = groupKeyFields(resource);
      if (
        typeof modelLabel !== "string" ||
        typeof groupsRoot !== "string" ||
        groupsRoot === "" ||
        typeof filterType !== "string" ||
        filterType === "" ||
        typeof groupByType !== "string" ||
        groupByType === "" ||
        typeof groupOrderType !== "string" ||
        groupOrderType === "" ||
        keyFields.length === 0
      ) {
        return [];
      }
      return [{
        modelLabel,
        groupsRoot,
        filterType,
        groupByType,
        groupOrderType,
        keyFields,
        measures: Array.isArray(resource?.aggregateMeasures)
          ? resource.aggregateMeasures
          : [],
      }];
    })
    .sort((left, right) => left.modelLabel.localeCompare(right.modelLabel));
}

function revisionFields(metadataPath) {
  const metadata = JSON.parse(readFileSync(metadataPath, "utf8"));
  const resources = metadata?.angee?.resources;
  if (!Array.isArray(resources)) return [];
  return resources
    .flatMap((resource) => {
      const modelLabel = resource?.modelLabel;
      const revisionsRoot = resource?.roots?.revisions;
      const revisionFields = resource?.revisionFields;
      if (
        typeof modelLabel !== "string" ||
        typeof revisionsRoot !== "string" ||
        revisionsRoot === "" ||
        !Array.isArray(revisionFields) ||
        revisionFields.length === 0
      ) {
        return [];
      }
      return [{
        modelLabel,
        revisionsRoot,
        fields: uniqueNames(["id", ...revisionFields]),
      }];
    })
    .sort((left, right) => left.modelLabel.localeCompare(right.modelLabel));
}

function saveFields(metadataPath) {
  const metadata = JSON.parse(readFileSync(metadataPath, "utf8"));
  const resources = metadata?.angee?.resources;
  if (!Array.isArray(resources)) return [];
  return resources
    .flatMap((resource) => {
      const modelLabel = resource?.modelLabel;
      const saveRoot = resource?.roots?.save;
      const lines = resource?.linesResource;
      if (
        typeof modelLabel !== "string" ||
        typeof saveRoot !== "string" ||
        saveRoot === "" ||
        !lines ||
        typeof lines.field !== "string" ||
        lines.field === ""
      ) {
        return [];
      }
      const patchType = resource?.typeNames?.updateInput;
      const linesInputType = lines.inputType;
      return [{
        modelLabel,
        saveRoot,
        // The `<res>_set_input` parent patch type and `<res>_lines_insert_input`
        // line type name the save mutation's `patch`/`lines` arguments; either is
        // omitted from the operation when the schema does not expose it.
        patchType: nonEmptyString(patchType),
        linesInputType: nonEmptyString(linesInputType),
        linesField: lines.field,
        selection: selectionFields(resource.fields, lines.field),
        linesSelection: selectionFields(lines.fields, null),
      }];
    })
    .sort((left, right) => left.modelLabel.localeCompare(right.modelLabel));
}

// The read selection for a save return type: the public `id`, then each readable
// scalar/enum field bare and each relation as `{ id <labelAxis?> }` — enough for
// the composing form to re-seed every declared field (and a picker's label) from
// the saved row. The lines list is added by its own child selection, so it is
// excluded here. Mirrors the parent form's own detail selection shape.
function selectionFields(fields, excludeField) {
  const parts = ["id"];
  const seen = new Set(["id"]);
  for (const field of fields ?? []) {
    const name = field?.name;
    if (
      typeof name !== "string" ||
      name === excludeField ||
      field?.readable !== true ||
      seen.has(name)
    ) {
      continue;
    }
    seen.add(name);
    if (field.kind === "relation") {
      parts.push(`${assertGraphQLName(name)} { id${relationLabelSelection(field)} }`);
    } else if (field.kind === "scalar" || field.kind === "enum") {
      parts.push(assertGraphQLName(name));
    }
    // A nested list (`kind === "list"`) is only the lines field here; skip it.
  }
  return parts;
}

function relationLabelSelection(field) {
  const axis = field?.relationLabelAxis;
  return typeof axis === "string" && axis !== "" && axis !== "id"
    ? ` ${assertGraphQLName(axis)}`
    : "";
}

function saveDocument(resource) {
  const variables = ["$pk: ID!"];
  const args = ["pk: $pk"];
  if (resource.patchType) {
    variables.push(`$patch: ${assertGraphQLName(resource.patchType)}`);
    args.push("patch: $patch");
  }
  if (resource.linesInputType) {
    variables.push(`$lines: [${assertGraphQLName(resource.linesInputType)}!]`);
    args.push("lines: $lines");
  }
  const linesSelection =
    resource.linesSelection.length > 0
      ? ` ${assertGraphQLName(resource.linesField)} { ${resource.linesSelection.join(" ")} }`
      : "";
  return parse(
    `mutation ${actionOperationName(resource.saveRoot)}(${variables.join(", ")}) { ` +
      `${resource.saveRoot}(${args.join(", ")}) { ` +
      `${resource.selection.join(" ")}${linesSelection} } }`,
    { noLocation: true },
  );
}

function nonEmptyString(value) {
  return typeof value === "string" && value !== "" ? value : null;
}

function actionDocument(field) {
  // The full in-band ActionResult surface: `id` lets the client deep-link to a
  // record the verb created, and `validation_errors` carries the field-keyed
  // (or non-field) domain-failure reasons the settle owner surfaces.
  return parse(
    `mutation ${actionOperationName(field)}($id: ID!) { ` +
      `${field}(id: $id) { ok message id validation_errors } }`,
    { noLocation: true },
  );
}

function aggregateDocument(root, filterType, measures) {
  return parse(
    `query ${actionOperationName(root)}($where: ${assertGraphQLName(filterType)}) { ` +
      `${root}(where: $where) { aggregate { ${aggregateSelection(measures)} } } }`,
    { noLocation: true },
  );
}

function groupDocument(resource) {
  return parse(
    `query ${actionOperationName(resource.groupsRoot)}(` +
      `$group_by: [${assertGraphQLName(resource.groupByType)}!]!, ` +
      `$where: ${assertGraphQLName(resource.filterType)}, ` +
      `$order_by: [${assertGraphQLName(resource.groupOrderType)}!], ` +
      "$limit: Int, $offset: Int) { " +
      `${assertGraphQLName(resource.groupsRoot)}(` +
      "group_by: $group_by, where: $where, order_by: $order_by, " +
      "limit: $limit, offset: $offset" +
      `) { key { ${groupKeySelection(resource.keyFields)} } ` +
      `aggregate { ${aggregateSelection(resource.measures)} } } }`,
    { noLocation: true },
  );
}

function deletePreviewDocument(root) {
  return parse(
    `mutation ${actionOperationName(root)}($id: ID!, $confirm: Boolean) { ` +
      `${root}(id: $id, confirm: $confirm) { ${DELETE_PREVIEW_SELECTION} } }`,
    { noLocation: true },
  );
}

function revisionDocument(root, fields) {
  return parse(
    `query ${actionOperationName(root)}($id: ID!) { ` +
      `${root}(id: $id) { ${fields.join(" ")} } }`,
    { noLocation: true },
  );
}

function groupKeyFields(resource) {
  const seen = new Set();
  const fields = [];
  for (const dimension of resource?.groupDimensions ?? []) {
    addGroupKeyField(fields, seen, dimension?.key, false);
    for (const extraction of dimension?.extractions ?? []) {
      addGroupKeyField(fields, seen, extraction?.key, false);
      addGroupKeyField(fields, seen, extraction?.rangeKey, true);
    }
  }
  return fields;
}

function addGroupKeyField(fields, seen, name, range) {
  if (typeof name !== "string" || name === "") return;
  const key = `${range ? "range" : "value"}:${name}`;
  if (seen.has(key)) return;
  seen.add(key);
  fields.push({ name: assertGraphQLName(name), range });
}

function groupKeySelection(fields) {
  return fields
    .map((field) => field.range ? `${field.name} { from to }` : field.name)
    .join(" ");
}

function aggregateSelection(measures) {
  const fieldsByOp = new Map();
  for (const measure of measures) {
    if (!measure || measure.op === "count") continue;
    const op = measure.op;
    if (!AGGREGATE_MEASURE_OPERATORS.includes(op)) {
      throw new Error(`Unsupported aggregate measure op in metadata: ${op}`);
    }
    const field = assertGraphQLName(measure.input ?? measure.field);
    const fields = fieldsByOp.get(op) ?? [];
    if (!fields.includes(field)) fields.push(field);
    fieldsByOp.set(op, fields);
  }
  return [
    "count",
    ...AGGREGATE_MEASURE_OPERATORS.flatMap((op) => {
      const fields = fieldsByOp.get(op);
      return fields && fields.length > 0
        ? [`${op} { ${fields.join(" ")} }`]
        : [];
    }),
  ].join(" ");
}

function actionOperationName(field) {
  return `Angee${field[0].toUpperCase()}${field.slice(1)}`;
}

function uniqueNames(fields) {
  return [...new Set(fields)].map(assertGraphQLName);
}

function assertGraphQLName(name) {
  if (typeof name !== "string" || !/^[_A-Za-z][_0-9A-Za-z]*$/.test(name)) {
    throw new Error(`Invalid GraphQL field name in operation document metadata: ${name}`);
  }
  return name;
}

function slash(value) {
  return value.replaceAll(path.sep, "/");
}
