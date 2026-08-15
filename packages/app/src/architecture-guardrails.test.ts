import { describe, expect, test } from "vitest";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
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

describe("React architecture guardrails", () => {
  test("framework and tooling imports follow declared package layering", () => {
    const packageRoots = [
      ...FRAMEWORK_PACKAGES.entries(),
      ...toolingPackageRoots().map((root) => [packageName(root), root] as const),
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
});

function toolingPackageRoots(): string[] {
  // The tooling packages are named deliberately, not discovered: this repo's
  // tree is the contract, and a stray directory must not join the layering.
  return ["storybook", "e2e"].filter((entry) =>
    existsSync(join(REPO_ROOT, entry, "package.json")),
  );
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

function angeePackageName(specifier: string): string | null {
  if (!specifier.startsWith("@angee/")) return null;
  const [scope, name] = specifier.split("/");
  return scope && name ? `${scope}/${name}` : null;
}
