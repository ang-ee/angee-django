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

/** Print Refine's field tree, optionally binding authored argument text by dotted path. */
export function selectionText(
  fields: Fields,
  argumentsByPath: Readonly<Record<string, string>> = {},
  parentPath = "",
): string {
  return fields.map((field) => {
    if (typeof field === "string") {
      const path = parentPath ? `${parentPath}.${field}` : field;
      return `${field}${argumentsByPath[path] ?? ""}`;
    }
    return Object.entries(field).map(([name, children]) => {
      const path = parentPath ? `${parentPath}.${name}` : name;
      return `${name}${argumentsByPath[path] ?? ""} { ${selectionText(children as Fields, argumentsByPath, path)} }`;
    }).join(" ");
  }).join(" ");
}

/** Check only the declared native projection; null is present, omission is not. */
export function selectedFieldsPresent(data: unknown, fields: Fields): boolean {
  if (data === null) return true;
  if (Array.isArray(data)) return data.every((row) => selectedFieldsPresent(row, fields));
  if (typeof data !== "object" || data === null) return false;
  const row = data as Record<string, unknown>;
  return fields.every((field) => typeof field === "string"
    ? Object.hasOwn(row, field) && row[field] !== undefined
    : Object.entries(field).every(([name, children]) => Object.hasOwn(row, name)
      && row[name] !== undefined && selectedFieldsPresent(row[name], children as Fields)));
}
