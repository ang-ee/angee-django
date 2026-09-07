import type { Fields } from "@refinedev/core";

type FieldTree = Map<string, FieldTree>;

/** Translate validated dotted paths to Refine's native selection shape. */
export function refineFieldsFromPaths(paths: readonly string[]): Fields {
  const root: FieldTree = new Map();
  for (const path of paths) {
    let node = root;
    for (const name of path.split(".")) {
      if (!/^[_A-Za-z][_0-9A-Za-z]*$/.test(name)) {
        throw new Error(`Invalid GraphQL selection path: ${path}`);
      }
      const child = node.get(name) ?? new Map<string, FieldTree>();
      node.set(name, child);
      node = child;
    }
  }
  const fields = (tree: FieldTree): Fields => [...tree].map(([name, child]) =>
    child.size ? { [name]: fields(child) } : name);
  return fields(root);
}

/** Print Refine's field tree without interpreting resource or query semantics. */
export function selectionText(fields: Fields): string {
  return fields.map((field) => typeof field === "string" ? field
    : Object.entries(field).map(([name, children]) => `${name} { ${selectionText(children as Fields)} }`).join(" ")).join(" ");
}
