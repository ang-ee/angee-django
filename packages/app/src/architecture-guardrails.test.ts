import { describe, expect, test } from "vitest";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { basename, dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

import { baseIcons } from "@angee/ui";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const WORKSPACE_ROOT = resolve(REPO_ROOT, "..");
const SOURCE_EXTENSIONS = new Set([".ts", ".tsx"]);
const DELETED_SHELLS = new Set([
  "@angee/base",
  "@angee/data",
  "@angee/sdk",
  "@angee/resources-addon",
]);
const FRAMEWORK_PACKAGES = new Map([
  ["@angee/app", "app"],
  ["@angee/refine", "refine"],
  ["@angee/metadata", "metadata"],
  ["@angee/ui", "ui"],
]);
const FRAMEWORK_IMPORT_RULES: Record<string, readonly string[]> = {
  "@angee/refine": [],
  "@angee/metadata": [],
  "@angee/ui": ["@angee/refine", "@angee/metadata"],
  "@angee/app": ["@angee/refine", "@angee/metadata", "@angee/ui"],
};
const REQUIRED_ADDON_REPOSITORIES = [
  "angee-base",
  "angee-messaging-bridges",
  "angee-examples",
] as const;
const MISSING_ADDON_REPOSITORIES = missingAddonRepositories();
const SKIP_WORKSPACE_GUARDRAILS = MISSING_ADDON_REPOSITORIES.length > 0;
const WORKSPACE_GUARDRAILS_NOTICE =
  `[architecture guardrails] Skipping workspace addon assertions; missing required addon repositories: ${MISSING_ADDON_REPOSITORIES.join(", ")}.`;

if (SKIP_WORKSPACE_GUARDRAILS) {
  if (process.env.ANGEE_WORKSPACE_GUARDRAILS === "require") {
    throw new Error(
      `Architecture guardrail is missing required addon repositories: ${MISSING_ADDON_REPOSITORIES.join(", ")}`,
    );
  }
}

interface PackageRoot {
  name: string;
  root: string;
}

interface CriticalExportDeclaration {
  name: string;
  ownerFile: string;
}

interface AddonGuardrailRegistry {
  criticalExports: readonly {
    name: string;
    ownerFile: string;
  }[];
  dynamicI18nKeyFamilies: readonly DynamicI18nKeyFamily[];
  allowedRelativeEscapes: readonly AllowedRelativeEscape[];
}

interface AllowedRelativeEscape {
  file: string;
  specifier: string;
  reason: string;
}

interface I18nBundleKey {
  namespace: string;
  key: string;
  file: string;
}

interface I18nSourceText {
  namespace: string;
  file: string;
  text: string;
}

interface DynamicI18nKeyFamily {
  namespace: string;
  owner: string;
  prefix: string;
  values: readonly string[];
  suffixes?: readonly string[];
}

const FRAMEWORK_CRITICAL_EXPORTS: readonly CriticalExportDeclaration[] = [
  frameworkCriticalExport("resourcePageRoutes", "@angee/app", "src/define-base-addon.ts"),
  frameworkCriticalExport("expectValidBaseAddon", "@angee/app", "src/testing.tsx"),
  frameworkCriticalExport("MutationDialog", "@angee/ui", "src/views/MutationDialog.tsx"),
  frameworkCriticalExport("ScopedExplorerPane", "@angee/ui", "src/views/ScopedExplorerPane.tsx"),
  frameworkCriticalExport("PrimaryPanePublisher", "@angee/ui", "src/layouts/primary-pane-context.tsx"),
  frameworkCriticalExport("useLatestRef", "@angee/ui", "src/lib/use-latest-ref.ts"),
  frameworkCriticalExport("useAngeeDeletePreview", "@angee/refine", "src/dialect/hooks.tsx"),
];

const UI_DYNAMIC_I18N_KEY_FAMILIES: readonly DynamicI18nKeyFamily[] = [
  {
    namespace: "ui",
    owner: "@refinedev/core resource action labels",
    prefix: "actions.",
    values: ["list", "create", "edit", "show", "delete", "clone"],
  },
  {
    namespace: "ui",
    owner: "@refinedev/core navigation button labels",
    prefix: "buttons.",
    values: ["list", "create", "edit", "show", "delete", "clone"],
  },
  {
    namespace: "ui",
    owner: "@angee/app login hero variants",
    prefix: "auth.hero.",
    values: ["intent", "agentNative", "composable"],
    suffixes: [".eyebrow", ".headline", ".body"],
  },
  {
    namespace: "ui",
    owner: "@angee/ui DrawerEdge",
    prefix: "drawer.rail.",
    values: ["right", "bottom"],
  },
  {
    namespace: "ui",
    owner: "@angee/ui CalendarViewMode",
    prefix: "calendar.mode.",
    values: ["month", "week", "day"],
  },
];

describe("React architecture guardrails", () => {
  if (SKIP_WORKSPACE_GUARDRAILS) {
    test("reports why workspace addon assertions are skipped", () => {
      console.warn(WORKSPACE_GUARDRAILS_NOTICE);
      expect(MISSING_ADDON_REPOSITORIES.length).toBeGreaterThan(0);
    });
  }

  test("framework and tooling imports follow layering and declared dependencies", () => {
    expect(importViolations(repositoryPackageRoots())).toEqual([]);
  });

  test.skipIf(SKIP_WORKSPACE_GUARDRAILS)(
    "framework, tooling, and addon imports follow layering and declared dependencies",
    () => {
      expect(importViolations(allPackageRoots())).toEqual([]);
    },
  );

  test("relative package escape detection reports a seeded violation", () => {
    const root = join(REPO_ROOT, "ui");
    const file = join(root, "src", "views", "ListView.tsx");
    const pkg = { name: "@angee/ui", root };

    expect(relativeImportEscapes(root, file, "../../../app/src/create-app")).toBe(true);
    expect(relativeImportEscapes(root, file, "../i18n")).toBe(false);
    expect(isApprovedRelativeEscape(pkg, file, "../../../app/src/create-app")).toBe(false);
  });

  test.skipIf(SKIP_WORKSPACE_GUARDRAILS)(
    "discovers every required addon repository from workspace siblings",
    () => {
      expect(addonRepositoryRoots().map((root) => basename(dirname(root)))).toEqual(
        expect.arrayContaining([...REQUIRED_ADDON_REPOSITORIES]),
      );
    },
  );

  test("addon guardrail registries reject malformed fields with their owning path", () => {
    expect(() => registryArray(
      { criticalExports: {} },
      "criticalExports",
      join(REPO_ROOT, "fixture", "architecture.guardrails.json"),
    )).toThrow(
      "Invalid addon guardrail registry angee-react/fixture/architecture.guardrails.json: "
      + "criticalExports must be an array",
    );
  });

  test("framework-owned dynamic i18n families contain only UI vocabulary", () => {
    expect([...new Set(UI_DYNAMIC_I18N_KEY_FAMILIES.map((family) => family.namespace))])
      .toEqual(["ui"]);
  });

  test.skipIf(SKIP_WORKSPACE_GUARDRAILS)(
    "declared addon web package edges stay acyclic",
    () => {
      const addons = addonPackageRoots();
      const addonNames = new Set(addons.map((pkg) => pkg.name));
      const edges = new Map(
        addons.map((pkg) => [
          pkg.name,
          [...packageDependencies(pkg.root)].filter((dependency) => addonNames.has(dependency)),
        ]),
      );

      expect(findCycles(edges)).toEqual([]);
    },
  );

  test("addon cycle detection reports a seeded violation", () => {
    expect(findCycles(new Map([
      ["@angee/a", ["@angee/b"]],
      ["@angee/b", ["@angee/a"]],
    ]))).toEqual(["@angee/a -> @angee/b -> @angee/a"]);
  });

  test("framework critical exports exist in their declared owners", () => {
    expect(invalidCriticalExports(FRAMEWORK_CRITICAL_EXPORTS)).toEqual([]);
  });

  test.skipIf(SKIP_WORKSPACE_GUARDRAILS)(
    "critical shared owners have a production consumer",
    () => {
      const declarations = [
        ...FRAMEWORK_CRITICAL_EXPORTS,
        ...addonCriticalExports(),
      ];
      expect(invalidCriticalExports(declarations)).toEqual([]);
      const contents = allPackageRoots().flatMap((pkg) =>
        sourceFiles(pkg.root).map((file) => ({
          file,
          text: readFileSync(file, "utf8"),
        })),
      );
      const unused = declarations
        .filter((declaration) => !contents.some((candidate) => {
          if (resolve(candidate.file) === resolve(declaration.ownerFile)) return false;
          if (isTestFile(candidate.file) || isStoryFile(candidate.file)) return false;
          return new RegExp(`\\b${escapeRegExp(declaration.name)}\\b`).test(candidate.text);
        }))
        .map((declaration) => declaration.name);

      expect(unused).toEqual([]);
    },
  );

  test("i18n bundle liveness flags a planted dead key", () => {
    const fixtureKeys: I18nBundleKey[] = [
      { namespace: "fixture", key: "live", file: "fixture/i18n.ts" },
      { namespace: "fixture", key: "items_one", file: "fixture/i18n.ts" },
      { namespace: "fixture", key: "items_other", file: "fixture/i18n.ts" },
      { namespace: "fixture", key: "planted.dead", file: "fixture/i18n.ts" },
      { namespace: "ui", key: "addon.only", file: "fixture/ui-i18n.ts" },
    ];
    const fixtureSources: I18nSourceText[] = [
      { namespace: "fixture", file: "fixture/view.tsx", text: 't("live"); t("items", { count })' },
      { namespace: "addon", file: "fixture/addon-view.tsx", text: 'useUiT()("addon.only")' },
    ];

    expect(unusedI18nKeys(fixtureKeys, fixtureSources, new Set())).toEqual([
      "fixture.planted.dead (angee-react/app/fixture/i18n.ts)",
    ]);
  });

  test("every repository i18n key is referenced statically or by a typed dynamic family", () => {
    const packages = repositoryPackageRoots();
    const bundles = i18nBundleKeys(packages);
    const bundleFiles = new Set(bundles.map((entry) => resolve(entry.file)));
    const sources = i18nSourceTexts(packages, bundleFiles);

    expect(unusedI18nKeys(bundles, sources, frameworkDynamicI18nKeys())).toEqual([]);
  });

  test.skipIf(SKIP_WORKSPACE_GUARDRAILS)(
    "every bundled i18n key is referenced statically or by a typed dynamic family",
    () => {
      const packages = allPackageRoots();
      const bundles = i18nBundleKeys(packages);
      const bundleFiles = new Set(bundles.map((entry) => resolve(entry.file)));
      const sources = i18nSourceTexts(packages, bundleFiles);

      expect(unusedI18nKeys(bundles, sources, dynamicI18nKeys())).toEqual([]);
    },
  );

  test("every framework-authored glyph literal resolves from the base registry", () => {
    const productionFiles = repositoryPackageRoots().flatMap((pkg) =>
      sourceFiles(pkg.root).filter((file) => !isTestFile(file) && !isStoryFile(file)),
    );
    const available = new Set<string>(Object.keys(baseIcons));
    for (const file of productionFiles) {
      for (const name of declaredIconNames(readFileSync(file, "utf8"))) available.add(name);
    }
    const missing = productionFiles.flatMap((file) =>
      glyphLiteralReferences(file)
        .filter((name) => !available.has(name))
        .map((name) => `${name} (${workspaceRelative(file)})`),
    );

    expect([...new Set(missing)].sort()).toEqual([]);
  });

  test.skipIf(SKIP_WORKSPACE_GUARDRAILS)(
    "every authored glyph literal resolves from the base or addon registries",
    () => {
      const productionFiles = allPackageRoots().flatMap((pkg) =>
        sourceFiles(pkg.root).filter((file) => !isTestFile(file) && !isStoryFile(file)),
      );
      // Icon lookup is deliberately composition-global and base-first: createApp
      // seeds baseIcons, then merges every addon registry. Cross-addon consumers
      // such as storage-integrate's `drive` are therefore valid at runtime.
      const available = new Set<string>(Object.keys(baseIcons));
      for (const file of productionFiles) {
        for (const name of declaredIconNames(readFileSync(file, "utf8"))) available.add(name);
      }
      const missing = productionFiles.flatMap((file) =>
        glyphLiteralReferences(file)
          .filter((name) => !available.has(name))
          .map((name) => `${name} (${workspaceRelative(file)})`),
      );

      expect([...new Set(missing)].sort()).toEqual([]);
    },
  );

  test("glyph extraction finds JSX and object literals without reading conditions or CSS variants", () => {
    const source = ts.createSourceFile(
      "fixture.tsx",
      `
        const menu = { icon: "server", iconName: "archive" };
        const styles = { size: { icon: "size-3" } };
        export const Example = ({ dark }: { dark: boolean }) => (
          <Glyph name={dark ? "moon" : "sun"} />
        );
      `,
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    );

    expect(glyphLiteralReferencesFromSource(source).sort()).toEqual([
      "archive",
      "moon",
      "server",
      "sun",
    ]);
  });
});

function frameworkCriticalExport(
  name: string,
  packageName: string,
  ownerFile: string,
): CriticalExportDeclaration {
  const packageRoot = resolve(REPO_ROOT, FRAMEWORK_PACKAGES.get(packageName) ?? "");
  return { name, ownerFile: join(packageRoot, ownerFile) };
}

function allPackageRoots(): PackageRoot[] {
  return [
    ...repositoryPackageRoots(),
    ...addonPackageRoots(),
  ];
}

function repositoryPackageRoots(): PackageRoot[] {
  return [
    ...[...FRAMEWORK_PACKAGES.entries()].map(([name, root]) => ({
      name,
      root: resolve(REPO_ROOT, root),
    })),
    ...toolingPackageRoots(),
  ];
}

function toolingPackageRoots(): PackageRoot[] {
  return ["storybook", "e2e"]
    .map((entry) => resolve(REPO_ROOT, entry))
    .filter((root) => existsSync(join(root, "package.json")))
    .map(packageRoot);
}

function addonPackageRoots(): PackageRoot[] {
  const roots: PackageRoot[] = [];
  for (const addonsRoot of addonRepositoryRoots()) {
    visitDirectories(addonsRoot, (directory) => {
      if (basename(directory) !== "web" || !existsSync(join(directory, "package.json"))) {
        return false;
      }
      roots.push(packageRoot(directory));
      return true;
    });
  }
  return roots.sort((left, right) => left.name.localeCompare(right.name));
}

function addonRepositoryRoots(): string[] {
  const roots = readdirSync(WORKSPACE_ROOT)
    .map((entry) => join(WORKSPACE_ROOT, entry, "addons"))
    .filter((root) => existsSync(root) && statSync(root).isDirectory());
  const discovered = new Set(roots.map((root) => basename(dirname(root))));
  const missing = REQUIRED_ADDON_REPOSITORIES.filter((name) => !discovered.has(name));
  if (missing.length > 0) {
    throw new Error(`Architecture guardrail is missing required addon repositories: ${missing.join(", ")}`);
  }
  return roots.sort();
}

function missingAddonRepositories(): string[] {
  if (process.env.ANGEE_WORKSPACE_GUARDRAILS === "skip") {
    return [...REQUIRED_ADDON_REPOSITORIES];
  }
  return REQUIRED_ADDON_REPOSITORIES.filter((name) => {
    const root = join(WORKSPACE_ROOT, name, "addons");
    return !existsSync(root) || !statSync(root).isDirectory();
  });
}

function importViolations(packages: readonly PackageRoot[]): string[] {
  const violations: string[] = [];
  for (const pkg of packages) {
    const dependencies = packageDependencies(pkg.root);
    for (const file of sourceFiles(pkg.root)) {
      for (const specifier of importSpecifiers(file)) {
        const rel = workspaceRelative(file);
        if (
          specifier.startsWith(".")
          && relativeImportEscapes(pkg.root, file, specifier)
          && !isApprovedRelativeEscape(pkg, file, specifier)
        ) {
          violations.push(`${rel} imports ${specifier}, outside package root ${workspaceRelative(pkg.root)}`);
          continue;
        }
        const importedPackage = angeePackageName(specifier);
        if (!importedPackage) continue;
        if (importedPackage === "@angee/gql" && isRepositoryPackage(pkg)) {
          violations.push(`${rel} imports project-generated schema package ${importedPackage}`);
          continue;
        }
        if (DELETED_SHELLS.has(importedPackage)) {
          violations.push(`${rel} imports deleted shell ${importedPackage}`);
          continue;
        }
        // Generated schemas are injected into sibling addon fragments by the
        // composed stack; only packages in this repository must reject them.
        if (
          importedPackage !== pkg.name
          && importedPackage !== "@angee/gql"
          && !dependencies.has(importedPackage)
        ) {
          violations.push(`${rel} imports ${importedPackage} without declaring it in package.json`);
        }
        if (FRAMEWORK_PACKAGES.has(pkg.name)) {
          const allowed = FRAMEWORK_IMPORT_RULES[pkg.name] ?? [];
          if (
            importedPackage !== pkg.name
            && FRAMEWORK_PACKAGES.has(importedPackage)
            && !allowed.includes(importedPackage)
          ) {
            violations.push(`${rel} imports ${importedPackage}, outside ${pkg.name}'s framework layer`);
          }
        }
      }
    }
  }
  return violations;
}

function isRepositoryPackage(pkg: PackageRoot): boolean {
  const rel = relative(REPO_ROOT, pkg.root);
  return rel !== ".." && !rel.startsWith(`..${sep}`);
}

function invalidCriticalExports(
  declarations: readonly CriticalExportDeclaration[],
): string[] {
  return declarations
    .filter((declaration) =>
      !existsSync(declaration.ownerFile)
      || !new RegExp(`\\b${escapeRegExp(declaration.name)}\\b`).test(
        readFileSync(declaration.ownerFile, "utf8"),
      ))
    .map((declaration) => `${declaration.name} (${workspaceRelative(declaration.ownerFile)})`);
}

function packageRoot(root: string): PackageRoot {
  return {
    name: JSON.parse(readFileSync(join(root, "package.json"), "utf8")).name,
    root,
  };
}

function visitDirectories(
  root: string,
  visitor: (directory: string) => boolean,
): void {
  if (visitor(root)) return;
  for (const entry of readdirSync(root)) {
    const child = join(root, entry);
    if (!statSync(child).isDirectory()) continue;
    if (entry === "node_modules" || entry === "runtime" || entry === ".git") continue;
    visitDirectories(child, visitor);
  }
}

function packageDependencies(root: string): ReadonlySet<string> {
  const manifest = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
  return new Set([
    ...Object.keys(manifest.dependencies ?? {}),
    ...Object.keys(manifest.devDependencies ?? {}),
    ...Object.keys(manifest.peerDependencies ?? {}),
  ]);
}

function sourceFiles(root: string): string[] {
  if (!existsSync(root)) return [];
  const files: string[] = [];
  const visit = (entry: string): void => {
    const stat = statSync(entry);
    if (stat.isDirectory()) {
      if (
        entry.includes("/node_modules/")
        || entry.includes("/runtime/")
        || entry.includes("/dist/")
        || entry.includes("/coverage/")
      ) return;
      for (const child of readdirSync(entry)) visit(join(entry, child));
      return;
    }
    const extension = entry.slice(entry.lastIndexOf("."));
    if (SOURCE_EXTENSIONS.has(extension)) files.push(entry);
  };
  visit(root);
  return files;
}

function importSpecifiers(file: string): string[] {
  const text = readFileSync(file, "utf8");
  const source = ts.createSourceFile(
    file,
    text,
    ts.ScriptTarget.Latest,
    true,
    file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const specifiers: string[] = [];
  const visit = (node: ts.Node): void => {
    const dynamicImportArgument = ts.isCallExpression(node)
      ? node.arguments[0]
      : undefined;
    if (
      (ts.isImportDeclaration(node) || ts.isExportDeclaration(node))
      && node.moduleSpecifier
      && ts.isStringLiteral(node.moduleSpecifier)
    ) {
      specifiers.push(node.moduleSpecifier.text);
    } else if (
      ts.isCallExpression(node)
      && node.expression.kind === ts.SyntaxKind.ImportKeyword
      && node.arguments.length === 1
      && dynamicImportArgument !== undefined
      && ts.isStringLiteral(dynamicImportArgument)
    ) {
      specifiers.push(dynamicImportArgument.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return specifiers;
}

function relativeImportEscapes(packageRootPath: string, file: string, specifier: string): boolean {
  const target = resolve(dirname(file), specifier);
  const rel = relative(packageRootPath, target);
  return rel === ".." || rel.startsWith(`..${sep}`);
}

function isApprovedRelativeEscape(
  pkg: PackageRoot,
  file: string,
  specifier: string,
): boolean {
  if (
    basename(file) === "vitest.config.ts"
    && basename(resolve(dirname(file), specifier)) === "vitest.shared"
  ) {
    return true;
  }

  const registry = addonGuardrailRegistry(pkg);
  return registry?.allowedRelativeEscapes.some((escape) =>
    resolve(pkg.root, escape.file) === resolve(file)
    && escape.specifier === specifier) ?? false;
}

function angeePackageName(specifier: string): string | null {
  if (!specifier.startsWith("@angee/")) return null;
  const [scope, name] = specifier.split("/");
  return scope && name ? `${scope}/${name}` : null;
}

function addonCriticalExports(): CriticalExportDeclaration[] {
  return addonPackageRoots().flatMap((pkg) => {
    const registry = addonGuardrailRegistry(pkg);
    if (!registry) return [];
    return registry.criticalExports.map((declaration) => ({
      name: declaration.name,
      ownerFile: join(pkg.root, declaration.ownerFile),
    }));
  });
}

function addonDynamicI18nKeyFamilies(): DynamicI18nKeyFamily[] {
  return addonPackageRoots().flatMap((pkg) =>
    addonGuardrailRegistry(pkg)?.dynamicI18nKeyFamilies ?? []);
}

function addonGuardrailRegistry(pkg: PackageRoot): AddonGuardrailRegistry | null {
  const file = join(pkg.root, "architecture.guardrails.json");
  if (!existsSync(file)) return null;
  const value: unknown = JSON.parse(readFileSync(file, "utf8"));
  if (!isRecord(value)) throw registryError(file, "root must be an object");

  const criticalExports = registryArray(value, "criticalExports", file).map((entry, index) => {
    const owner = registryRecord(entry, file, `criticalExports[${index}]`);
    return {
      name: registryString(owner, "name", file, `criticalExports[${index}]`),
      ownerFile: registryString(owner, "ownerFile", file, `criticalExports[${index}]`),
    };
  });
  const dynamicI18nKeyFamilies = registryArray(value, "dynamicI18nKeyFamilies", file)
    .map((entry, index) => {
      const owner = registryRecord(entry, file, `dynamicI18nKeyFamilies[${index}]`);
      const family: DynamicI18nKeyFamily = {
        namespace: registryString(owner, "namespace", file, `dynamicI18nKeyFamilies[${index}]`),
        owner: registryString(owner, "owner", file, `dynamicI18nKeyFamilies[${index}]`),
        prefix: registryStringAllowEmpty(
          owner,
          "prefix",
          file,
          `dynamicI18nKeyFamilies[${index}]`,
        ),
        values: registryStringArray(owner, "values", file, `dynamicI18nKeyFamilies[${index}]`),
      };
      const suffixes = registryOptionalStringArray(
        owner,
        "suffixes",
        file,
        `dynamicI18nKeyFamilies[${index}]`,
      );
      return suffixes ? { ...family, suffixes } : family;
    });
  const allowedRelativeEscapes = registryArray(value, "allowedRelativeEscapes", file)
    .map((entry, index) => {
      const owner = registryRecord(entry, file, `allowedRelativeEscapes[${index}]`);
      return {
        file: registryString(owner, "file", file, `allowedRelativeEscapes[${index}]`),
        specifier: registryString(owner, "specifier", file, `allowedRelativeEscapes[${index}]`),
        reason: registryString(owner, "reason", file, `allowedRelativeEscapes[${index}]`),
      };
    });

  return { criticalExports, dynamicI18nKeyFamilies, allowedRelativeEscapes };
}

function registryArray(
  registry: Record<string, unknown>,
  field: string,
  file: string,
): unknown[] {
  const value = registry[field];
  if (value === undefined) return [];
  if (!Array.isArray(value)) throw registryError(file, `${field} must be an array`);
  return value;
}

function registryRecord(
  value: unknown,
  file: string,
  location: string,
): Record<string, unknown> {
  if (!isRecord(value)) throw registryError(file, `${location} must be an object`);
  return value;
}

function registryString(
  value: Record<string, unknown>,
  field: string,
  file: string,
  location: string,
): string {
  const fieldValue = value[field];
  if (typeof fieldValue !== "string" || fieldValue.length === 0) {
    throw registryError(file, `${location}.${field} must be a non-empty string`);
  }
  return fieldValue;
}

function registryStringAllowEmpty(
  value: Record<string, unknown>,
  field: string,
  file: string,
  location: string,
): string {
  const fieldValue = value[field];
  if (typeof fieldValue !== "string") {
    throw registryError(file, `${location}.${field} must be a string`);
  }
  return fieldValue;
}

function registryStringArray(
  value: Record<string, unknown>,
  field: string,
  file: string,
  location: string,
): string[] {
  const strings = registryOptionalStringArray(value, field, file, location);
  if (!strings) throw registryError(file, `${location}.${field} must be an array of strings`);
  return strings;
}

function registryOptionalStringArray(
  value: Record<string, unknown>,
  field: string,
  file: string,
  location: string,
): string[] | undefined {
  const fieldValue = value[field];
  if (fieldValue === undefined) return undefined;
  if (!Array.isArray(fieldValue) || fieldValue.some((entry) => typeof entry !== "string")) {
    throw registryError(file, `${location}.${field} must be an array of strings`);
  }
  return fieldValue;
}

function registryError(file: string, message: string): Error {
  return new Error(`Invalid addon guardrail registry ${workspaceRelative(file)}: ${message}`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function i18nBundleKeys(packages: readonly PackageRoot[]): I18nBundleKey[] {
  const bundleFiles = [
    join(REPO_ROOT, "ui", "src", "i18n", "en.ts"),
    ...packages.map((pkg) => join(pkg.root, "src", "i18n.ts")),
  ].filter((file) => existsSync(file));
  return bundleFiles.flatMap((file) => {
    const text = readFileSync(file, "utf8");
    const keys = [...text.matchAll(/^\s*"([^"]+)"\s*:/gm)];
    if (keys.length === 0) return [];
    const namespace = i18nNamespace(file, text);
    return keys.map((match) => ({
      namespace,
      key: match[1] ?? "",
      file,
    }));
  });
}

function i18nNamespace(file: string, text: string): string {
  if (resolve(file) === resolve(REPO_ROOT, "ui", "src", "i18n", "en.ts")) return "ui";
  const declared = text.match(/createNamespaceT\(\s*["']([^"']+)["']/)?.[1];
  if (declared) return declared;
  const packageRootPath = resolve(dirname(file), "..");
  const manifestNamespaces = sourceFiles(packageRootPath).flatMap((sourceFile) => {
    if (isTestFile(sourceFile) || isStoryFile(sourceFile)) return [];
    const sourceText = readFileSync(sourceFile, "utf8");
    return [...sourceText.matchAll(/\bi18n\s*:\s*\{\s*(?:["']([^"']+)["']|([a-zA-Z][\w-]*))\s*:/g)]
      .map((match) => match[1] ?? match[2])
      .filter((namespace): namespace is string => Boolean(namespace));
  });
  const namespaces = [...new Set(manifestNamespaces)];
  if (namespaces.length === 1) return namespaces[0] ?? "";
  throw new Error(
    `Could not resolve one owning i18n namespace for ${workspaceRelative(file)}; `
    + "declare it with createNamespaceT or the addon's i18n manifest contribution",
  );
}

function i18nSourceTexts(
  packages: readonly PackageRoot[],
  bundleFiles: ReadonlySet<string>,
): I18nSourceText[] {
  return packages.flatMap((pkg) => {
    const namespaces = namespacesConsumedByPackage(pkg);
    return sourceFiles(pkg.root)
      .filter((file) => !bundleFiles.has(resolve(file)) && !isTestFile(file) && !isStoryFile(file))
      .flatMap((file) => namespaces.map((namespace) => ({
        namespace,
        file,
        text: readFileSync(file, "utf8"),
      })));
  });
}

function namespacesConsumedByPackage(pkg: PackageRoot): readonly string[] {
  const namespaces = new Set<string>(["ui"]);
  const bundleFile = join(pkg.root, "src", "i18n.ts");
  if (existsSync(bundleFile)) {
    const text = readFileSync(bundleFile, "utf8");
    if (/^\s*"[^"]+"\s*:/m.test(text)) {
      namespaces.add(i18nNamespace(bundleFile, text));
    }
  }
  return [...namespaces];
}

function dynamicI18nKeys(): ReadonlySet<string> {
  return dynamicI18nKeysFor(addonDynamicI18nKeyFamilies());
}

function frameworkDynamicI18nKeys(): ReadonlySet<string> {
  return dynamicI18nKeysFor([]);
}

function dynamicI18nKeysFor(
  addonFamilies: readonly DynamicI18nKeyFamily[],
): ReadonlySet<string> {
  const keys = new Set<string>();
  for (const family of [
    ...UI_DYNAMIC_I18N_KEY_FAMILIES,
    ...addonFamilies,
  ]) {
    const suffixes = family.suffixes ?? [""];
    for (const value of family.values) {
      for (const suffix of suffixes) {
        keys.add(`${family.namespace}\0${family.prefix}${value}${suffix}`);
      }
    }
  }
  return keys;
}

function unusedI18nKeys(
  bundleKeys: readonly I18nBundleKey[],
  sourceTexts: readonly I18nSourceText[],
  dynamicKeys: ReadonlySet<string>,
): string[] {
  return bundleKeys
    .filter((entry) => {
      if (dynamicKeys.has(`${entry.namespace}\0${entry.key}`)) return false;
      const referencedKey = entry.key.replace(/_(?:one|other)$/, "");
      return !sourceTexts.some((source) =>
        (entry.namespace === "ui" || source.namespace === entry.namespace)
        && containsQuotedKey(source.text, referencedKey));
    })
    .map((entry) => `${entry.namespace}.${entry.key} (${workspaceRelative(entry.file)})`)
    .sort();
}

function containsQuotedKey(text: string, key: string): boolean {
  return text.includes(`"${key}"`) || text.includes(`'${key}'`) || text.includes(`\`${key}\``);
}

function declaredIconNames(text: string): string[] {
  return [...text.matchAll(/\bicons\s*:\s*\{([\s\S]*?)\}/g)].flatMap((block) =>
    [...(block[1] ?? "").matchAll(/(?:^|,)\s*(?:"([^"]+)"|'([^']+)'|([a-zA-Z][\w-]*))\s*:/gm)]
      .map((match) => match[1] ?? match[2] ?? match[3])
      .filter((name): name is string => Boolean(name)),
  );
}

function glyphLiteralReferences(file: string): string[] {
  const source = ts.createSourceFile(
    file,
    readFileSync(file, "utf8"),
    ts.ScriptTarget.Latest,
    true,
    file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  return glyphLiteralReferencesFromSource(source);
}

function glyphLiteralReferencesFromSource(source: ts.SourceFile): string[] {
  const names: string[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isJsxOpeningLikeElement(node) && node.tagName.getText(source) === "Glyph") {
      const name = node.attributes.properties.find((attribute) =>
        ts.isJsxAttribute(attribute) && attribute.name.getText(source) === "name");
      if (name && ts.isJsxAttribute(name) && name.initializer) {
        if (ts.isStringLiteral(name.initializer)) names.push(name.initializer.text);
        else if (ts.isJsxExpression(name.initializer) && name.initializer.expression) {
          names.push(...glyphExpressionLiterals(name.initializer.expression));
        }
      }
    } else if (ts.isPropertyAssignment(node)) {
      const propertyName = node.name.getText(source).replace(/^["']|["']$/g, "");
      if (propertyName === "icon" || propertyName === "iconName") {
        names.push(...glyphExpressionLiterals(node.initializer));
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return names.filter(isGlyphNameLiteral);
}

function glyphExpressionLiterals(expression: ts.Expression): string[] {
  if (ts.isStringLiteralLike(expression)) return [expression.text];
  if (ts.isConditionalExpression(expression)) {
    return [
      ...glyphExpressionLiterals(expression.whenTrue),
      ...glyphExpressionLiterals(expression.whenFalse),
    ];
  }
  if (
    ts.isBinaryExpression(expression)
    && (
      expression.operatorToken.kind === ts.SyntaxKind.QuestionQuestionToken
      || expression.operatorToken.kind === ts.SyntaxKind.BarBarToken
    )
  ) {
    return [
      ...glyphExpressionLiterals(expression.left),
      ...glyphExpressionLiterals(expression.right),
    ];
  }
  if (
    ts.isParenthesizedExpression(expression)
    || ts.isAsExpression(expression)
    || ts.isTypeAssertionExpression(expression)
    || ts.isSatisfiesExpression(expression)
    || ts.isNonNullExpression(expression)
  ) {
    return glyphExpressionLiterals(expression.expression);
  }
  return [];
}

function isGlyphNameLiteral(value: string): boolean {
  return /^[a-z][a-z0-9-]*$/.test(value) && !/^size-\d/.test(value);
}

function isTestFile(file: string): boolean {
  return /\.(?:test|spec)\.[^.]+$/.test(file);
}

function isStoryFile(file: string): boolean {
  return /\.stories\.[^.]+$/.test(file);
}

function workspaceRelative(file: string): string {
  return relative(WORKSPACE_ROOT, file);
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function findCycles(edges: ReadonlyMap<string, readonly string[]>): string[] {
  const cycles = new Set<string>();
  const visiting = new Set<string>();
  const visited = new Set<string>();
  const stack: string[] = [];

  const visit = (node: string): void => {
    if (visiting.has(node)) {
      const cycleStart = stack.indexOf(node);
      if (cycleStart >= 0) cycles.add([...stack.slice(cycleStart), node].join(" -> "));
      return;
    }
    if (visited.has(node)) return;
    visiting.add(node);
    stack.push(node);
    for (const dependency of edges.get(node) ?? []) visit(dependency);
    stack.pop();
    visiting.delete(node);
    visited.add(node);
  };

  for (const node of edges.keys()) visit(node);
  return [...cycles].sort();
}
