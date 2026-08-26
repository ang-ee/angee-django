import { existsSync, readFileSync } from "node:fs";

const packageRoot = new URL("../", import.meta.url);
const packageJson = JSON.parse(
  readFileSync(new URL("package.json", packageRoot), "utf8"),
);

const viewEntries = (exports) =>
  Object.entries(exports).filter(([specifier]) => specifier.startsWith("./views/"));

const development = viewEntries(packageJson.exports);
const published = viewEntries(packageJson.publishConfig.exports);
const developmentKeys = development.map(([specifier]) => specifier);
const publishedKeys = published.map(([specifier]) => specifier);

if (developmentKeys.some((specifier) => specifier.includes("*"))) {
  throw new Error("View exports must be explicit; wildcard key found");
}
if (JSON.stringify(developmentKeys) !== JSON.stringify(publishedKeys)) {
  throw new Error("Development and publishConfig view export keys differ");
}

const rows = development.map(([specifier, developmentTarget], index) => {
  const publishedTarget = published[index][1];
  const source = developmentTarget.import;
  const dist = publishedTarget.default;

  for (const target of [developmentTarget.types, source, publishedTarget.types, dist]) {
    if (!existsSync(new URL(target, packageRoot))) {
      throw new Error(`${specifier} points to missing target ${target}`);
    }
  }

  return { specifier, source, dist };
});

console.table(rows);
console.log(`Verified ${rows.length} explicit view export keys and their source/dist targets.`);
