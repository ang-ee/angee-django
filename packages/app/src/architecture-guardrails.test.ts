import { describe, expect, test } from "vitest";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const SOURCE_EXTENSIONS = new Set([".ts", ".tsx"]);
const DELETED_SHELLS = new Set([
  "@angee/base",
  "@angee/data",
  "@angee/sdk",
  "@angee/resources-addon",
]);
const FRAMEWORK_PACKAGES = new Map([
  ["@angee/app", "angee/web/app"],
  ["@angee/refine", "angee/web/refine"],
  ["@angee/metadata", "angee/web/metadata"],
  ["@angee/ui", "angee/web/ui"],
]);
const FRAMEWORK_IMPORT_RULES: Record<string, readonly string[]> = {
  "@angee/refine": [],
  "@angee/metadata": [],
  "@angee/ui": ["@angee/refine", "@angee/metadata"],
  "@angee/app": ["@angee/refine", "@angee/metadata", "@angee/ui"],
};
const CRITICAL_OWNER_EXPORTS: Record<string, RegExp> = {
  resourcePageRoutes: /\bresourcePageRoutes\b/g,
  expectValidBaseAddon: /\bexpectValidBaseAddon\b/g,
  MutationDialog: /\bMutationDialog\b/g,
  ScopedExplorerPane: /\bScopedExplorerPane\b/g,
  PrimaryPanePublisher: /\bPrimaryPanePublisher\b/g,
  useLatestRef: /\buseLatestRef\b/g,
  useAngeeDeletePreview: /\buseAngeeDeletePreview\b/g,
};

describe("web architecture guardrails", () => {
  test("framework and addon imports follow declared package layering", () => {
    const packageRoots = [
      ...FRAMEWORK_PACKAGES.entries(),
      ...addonPackageRoots().map((root) => [packageName(root), root] as const),
    ];
    const packageDeps = new Map(
      packageRoots.map(([name, root]) => [name, packageDependencies(root)]),
    );
    const violations: string[] = [];

    for (const [packageName, packageRoot] of packageRoots) {
      for (const file of sourceFiles(packageRoot)) {
        for (const specifier of importSpecifiers(file)) {
          const importedPackage = angeePackageName(specifier);
          if (!importedPackage) continue;
          const rel = relative(REPO_ROOT, file);
          if (DELETED_SHELLS.has(importedPackage)) {
            violations.push(`${rel} imports deleted shell ${importedPackage}`);
            continue;
          }
          if (importedPackage === "@angee/gql") continue;
          if (FRAMEWORK_PACKAGES.has(packageName)) {
            const allowed = FRAMEWORK_IMPORT_RULES[packageName] ?? [];
            if (
              importedPackage !== packageName
              && FRAMEWORK_PACKAGES.has(importedPackage)
              && !allowed.includes(importedPackage)
            ) {
              violations.push(
                `${rel} imports ${importedPackage}, outside ${packageName}'s framework layer`,
              );
            }
            continue;
          }
          if (
            importedPackage !== packageName
            && !FRAMEWORK_PACKAGES.has(importedPackage)
            && !packageDeps.get(packageName)?.has(importedPackage)
          ) {
            violations.push(
              `${rel} imports ${importedPackage} without declaring it in package.json`,
            );
          }
        }
      }
    }

    expect(violations).toEqual([]);
  });

  test("declared addon web package edges stay acyclic", () => {
    const addonRoots = addonPackageRoots().map(
      (root) => [packageName(root), root] as const,
    );
    const addonPackages = new Set(addonRoots.map(([name]) => name));
    const addonEdges = new Map(
      addonRoots.map(([name, root]) => [
        name,
        [...packageDependencies(root)].filter((dependency) =>
          addonPackages.has(dependency),
        ),
      ]),
    );

    expect(findCycles(addonEdges)).toEqual([]);
  });

  test("addon cycle detection reports a seeded violation", () => {
    expect(
      findCycles(
        new Map([
          ["@angee/a", ["@angee/b"]],
          ["@angee/b", ["@angee/a"]],
        ]),
      ),
    ).toEqual(["@angee/a -> @angee/b -> @angee/a"]);
  });

  test("critical shared owners are consumed outside their defining package", () => {
    const contents = [
      ...sourceFiles("angee/web"),
      ...sourceFiles("addons/angee"),
      ...sourceFiles("examples/notes-angee/web"),
      ...sourceFiles("packages/storybook"),
    ]
      .filter((file) => !relative(REPO_ROOT, file).includes("/node_modules/"))
      .map((file) => ({
        file: relative(REPO_ROOT, file),
        text: readFileSync(file, "utf8"),
      }));
    const unused = Object.entries(CRITICAL_OWNER_EXPORTS)
      .filter(([name, pattern]) => {
        const hits = contents.filter(({ file, text }) => {
          pattern.lastIndex = 0;
          return pattern.test(text) && !isOwnerDefinitionFile(name, file);
        });
        return hits.length === 0;
      })
      .map(([name]) => name);

    expect(unused).toEqual([]);
  });

  test("row identity helpers are imported from the metadata owner", () => {
    const violations = [
      ...sourceFiles("angee/web"),
      ...sourceFiles("addons/angee"),
      ...sourceFiles("examples/notes-angee/web"),
      ...sourceFiles("packages/storybook"),
    ]
      .filter((file) => {
        const text = readFileSync(file, "utf8");
        return importsNamedBindingFrom(text, "rowPublicId", "@angee/resources");
      })
      .map((file) => relative(REPO_ROOT, file));

    expect(violations).toEqual([]);
  });

  test("authored GraphQL hooks are imported from the refine owner", () => {
    const violations = [
      ...sourceFiles("angee/web"),
      ...sourceFiles("addons/angee"),
      ...sourceFiles("examples/notes-angee/web"),
      ...sourceFiles("packages/storybook"),
    ]
      .filter((file) => {
        const text = readFileSync(file, "utf8");
        return (
          importsNamedBindingFrom(text, "useAuthoredMutation", "@angee/ui") ||
          importsNamedBindingFrom(text, "useAuthoredQuery", "@angee/ui")
        );
      })
      .map((file) => relative(REPO_ROOT, file));

    expect(violations).toEqual([]);
  });
});

function addonPackageRoots(): string[] {
  const root = join(REPO_ROOT, "addons/angee");
  return readdirSync(root)
    .map((entry) => join(root, entry, "web"))
    .filter((entry) => existsSync(join(entry, "package.json")))
    .map((entry) => relative(REPO_ROOT, entry));
}

function packageName(packageRoot: string): string {
  return JSON.parse(readFileSync(join(REPO_ROOT, packageRoot, "package.json"), "utf8")).name;
}

function packageDependencies(packageRoot: string): ReadonlySet<string> {
  const manifest = JSON.parse(readFileSync(join(REPO_ROOT, packageRoot, "package.json"), "utf8"));
  return new Set([
    ...Object.keys(manifest.dependencies ?? {}),
    ...Object.keys(manifest.devDependencies ?? {}),
    ...Object.keys(manifest.peerDependencies ?? {}),
  ]);
}

function sourceFiles(root: string): string[] {
  const absolute = resolve(REPO_ROOT, root);
  if (!existsSync(absolute)) return [];
  const files: string[] = [];
  const visit = (entry: string): void => {
    const stat = statSync(entry);
    if (stat.isDirectory()) {
      if (entry.includes("/node_modules/") || entry.includes("/runtime/")) return;
      for (const child of readdirSync(entry)) visit(join(entry, child));
      return;
    }
    if (SOURCE_EXTENSIONS.has(entry.slice(entry.lastIndexOf(".")))) files.push(entry);
  };
  visit(absolute);
  return files;
}

function importSpecifiers(file: string): string[] {
  const text = readFileSync(file, "utf8");
  const imports = text.matchAll(
    /\bfrom\s+["']([^"']+)["']|\bimport\s*\(\s*["']([^"']+)["']\s*\)|^\s*import\s+["']([^"']+)["']/gm,
  );
  return [...imports]
    .map((match) => match[1] ?? match[2] ?? match[3])
    .filter((specifier): specifier is string => Boolean(specifier));
}

function importsNamedBindingFrom(text: string, binding: string, specifier: string): boolean {
  const escapedSpecifier = specifier.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const imports = text.matchAll(
    new RegExp(`\\bimport\\s*\\{([^}]*)\\}\\s*from\\s*["']${escapedSpecifier}["']`, "g"),
  );
  for (const match of imports) {
    if (new RegExp(`\\b${binding}\\b`).test(match[1] ?? "")) return true;
  }
  return false;
}

function angeePackageName(specifier: string): string | null {
  if (!specifier.startsWith("@angee/")) return null;
  const [scope, name] = specifier.split("/");
  return scope && name ? `${scope}/${name}` : null;
}

function isOwnerDefinitionFile(name: string, file: string): boolean {
  if (name === "resourcePageRoutes") return file.endsWith("define-base-addon.ts");
  if (name === "expectValidBaseAddon") return file.endsWith("testing.tsx");
  if (name === "MutationDialog") return file.endsWith("MutationDialog.tsx");
  if (name === "ScopedExplorerPane") return file.endsWith("ScopedExplorerPane.tsx");
  if (name === "PrimaryPanePublisher") return file.endsWith("primary-pane-context.tsx");
  if (name === "useLatestRef") return file.endsWith("use-latest-ref.ts");
  if (name === "useAngeeDeletePreview") return file.endsWith("dialect/hooks.tsx");
  return false;
}

function findCycles(edges: ReadonlyMap<string, readonly string[]>): string[] {
  const cycles = new Set<string>();
  const visiting = new Set<string>();
  const visited = new Set<string>();
  const stack: string[] = [];

  const visit = (node: string): void => {
    if (visiting.has(node)) {
      const cycleStart = stack.indexOf(node);
      if (cycleStart >= 0) {
        cycles.add([...stack.slice(cycleStart), node].join(" -> "));
      }
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
