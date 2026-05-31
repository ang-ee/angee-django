// Pure extractors for the single-root-field documents the builder emits. Each
// resource document selects exactly one root field (the node, the connection,
// or the mutation payload), so extraction starts by reading that one value.

export type Row = Record<string, unknown>;

export interface PageInfo {
  endCursor: string | null;
  hasNextPage: boolean;
}

export interface ConnectionResult {
  rows: readonly Row[];
  total: number | undefined;
  pageInfo: PageInfo | undefined;
}

function isRecord(value: unknown): value is Row {
  return typeof value === "object" && value !== null;
}

/** The one root field's value from a response `data` object. */
function rootValue(data: unknown): unknown {
  if (!isRecord(data)) return undefined;
  for (const value of Object.values(data)) return value;
  return undefined;
}

/** The record a detail / mutation document returns, or null. */
export function extractNode(data: unknown): Row | null {
  const value = rootValue(data);
  return isRecord(value) ? value : null;
}

/** The rows, total, and page info a relay connection document returns. */
export function extractConnection(data: unknown): ConnectionResult {
  const connection = rootValue(data);
  if (!isRecord(connection)) {
    return { rows: [], total: undefined, pageInfo: undefined };
  }
  const edges = Array.isArray(connection.edges) ? connection.edges : [];
  return {
    rows: edges
      .map((edge) => (isRecord(edge) ? edge.node : undefined))
      .filter(isRecord),
    total: typeof connection.totalCount === "number" ? connection.totalCount : undefined,
    pageInfo: isRecord(connection.pageInfo)
      ? (connection.pageInfo as unknown as PageInfo)
      : undefined,
  };
}
