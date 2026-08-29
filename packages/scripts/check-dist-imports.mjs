#!/usr/bin/env node
// Post-build guard for the published packages: every relative import specifier
// in every dist/**/*.js must resolve to a file inside that package's dist.
// This is what catches the app build's src->dist re-rooting silently breaking
// (a future src<->config cross-import compiles fine but ships unresolvable
// paths), and any dist layout drift in the other packages.

import { readdirSync, readFileSync, statSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

const PACKAGES = ["refine", "metadata", "ui", "app", "e2e"];
const IMPORT_RE =
  /(?:^|[^.\w])(?:import|export)\s[^"']*?from\s*["'](\.[^"']+)["']|(?:^|[^.\w])import\s*\(\s*["'](\.[^"']+)["']\s*\)|(?:^|[^.\w])import\s*["'](\.[^"']+)["']/g;

function* jsFiles(dir) {
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry);
    if (statSync(p).isDirectory()) yield* jsFiles(p);
    else if (p.endsWith(".js") || p.endsWith(".mjs")) yield p;
  }
}

function resolves(from, spec) {
  const base = resolve(dirname(from), spec);
  return [base, `${base}.js`, `${base}.mjs`, join(base, "index.js")].some(
    (c) => existsSync(c) && statSync(c).isFile(),
  );
}

let files = 0;
let imports = 0;
const broken = [];
for (const pkg of PACKAGES) {
  const dist = resolve(import.meta.dirname, "..", pkg, "dist");
  if (!existsSync(dist)) {
    console.error(`check-dist-imports: ${pkg}/dist missing — run the build first`);
    process.exit(1);
  }
  for (const file of jsFiles(dist)) {
    files += 1;
    const source = readFileSync(file, "utf8");
    for (const match of source.matchAll(IMPORT_RE)) {
      const spec = match[1] ?? match[2] ?? match[3];
      // CSS side-effect imports ship alongside the JS; verify the file exists.
      imports += 1;
      if (!resolves(file, spec)) broken.push(`${file} -> ${spec}`);
    }
  }
}

if (broken.length) {
  console.error(`check-dist-imports: ${broken.length} unresolved relative import(s):`);
  for (const b of broken) console.error(`  ${b}`);
  process.exit(1);
}
console.log(
  `check-dist-imports: OK — ${imports} relative imports across ${files} files in ${PACKAGES.length} packages`,
);
