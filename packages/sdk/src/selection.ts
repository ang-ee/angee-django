const GRAPHQL_NAME = /^[_A-Za-z][_0-9A-Za-z]*$/;

function assertName(name: string): string {
  if (!GRAPHQL_NAME.test(name)) {
    throw new Error(`Invalid GraphQL field name: ${name}`);
  }
  return name;
}

/**
 * The GraphQL type name for a model label. Accepts a bare type name (`Note`) or
 * a Django label whose final segment is the type (`notes.Note`); the first
 * letter is upper-cased so the result is a valid type name either way.
 */
export function typeNameForModel(modelLabel: string): string {
  const segment = modelLabel.split(".").pop() ?? "";
  const name = assertName(segment);
  return name.charAt(0).toUpperCase() + name.slice(1);
}
