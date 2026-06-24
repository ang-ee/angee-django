export interface SelectionField {
  name: string;
  children?: SelectionField[];
}

const GRAPHQL_NAME = /^[_A-Za-z][_0-9A-Za-z]*$/;

function assertSelectionName(name: string): string {
  if (!GRAPHQL_NAME.test(name)) {
    throw new Error(`Invalid GraphQL field name: ${name}`);
  }
  return name;
}

export function buildSelection(fieldPaths: readonly string[]): SelectionField[] {
  const root: SelectionField[] = [];
  ensureLeaf(root, "id");
  for (const path of fieldPaths) {
    addPath(
      root,
      path.split(".").filter(Boolean).map(assertSelectionName),
    );
  }
  return root;
}

function addPath(into: SelectionField[], segments: readonly string[]): void {
  const [head, ...rest] = segments;
  if (head === undefined) return;
  if (rest.length === 0) {
    ensureLeaf(into, head);
    return;
  }
  const branch = ensureBranch(into, head);
  ensureLeaf(branch, "id");
  addPath(branch, rest);
}

function ensureLeaf(into: SelectionField[], name: string): void {
  if (!into.some((node) => node.name === name)) into.push({ name });
}

function ensureBranch(into: SelectionField[], name: string): SelectionField[] {
  const existing = into.find((node) => node.name === name);
  if (existing) return (existing.children ??= []);
  const children: SelectionField[] = [];
  into.push({ name, children });
  return children;
}

export function printSelection(fields: readonly SelectionField[]): string {
  return fields
    .map((field) =>
      field.children && field.children.length > 0
        ? `${field.name} { ${printSelection(field.children)} }`
        : field.name,
    )
    .join(" ");
}
