// Pure extractors for the single-root-field documents the builder emits. Each
// resource document selects exactly one root field (the node, the page, or the
// mutation payload), so extraction starts by reading that one value.

export type Row = Record<string, unknown>;

/** An offset page's echoed window: where it starts and how many it asked for. */
export interface PageInfo {
  offset: number;
  limit: number | null;
}

export interface PageResult {
  rows: readonly Row[];
  total: number | undefined;
  pageInfo: PageInfo | undefined;
}

export interface DeletePreviewGroup {
  label: string;
  count: number;
}

export interface DeletePreviewNode {
  label: string;
  objectLabel: string;
  objectId: string | null;
  children: readonly DeletePreviewNode[];
}

export interface DeletePreview {
  totalDeletedCount: number;
  deleted: readonly DeletePreviewGroup[];
  updated: readonly DeletePreviewGroup[];
  blocked: readonly DeletePreviewGroup[];
  hasBlockers: boolean;
  root: DeletePreviewNode;
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

/** The rows, total, and page info an offset-paginated document returns. */
export function extractPage(data: unknown): PageResult {
  const page = rootValue(data);
  if (!isRecord(page)) {
    return { rows: [], total: undefined, pageInfo: undefined };
  }
  const results = Array.isArray(page.results) ? page.results : [];
  return {
    rows: results.filter(isRecord),
    total: typeof page.totalCount === "number" ? page.totalCount : undefined,
    pageInfo: toPageInfo(page.pageInfo),
  };
}

/** The cascade preview a delete mutation returns, or null. */
export function extractDeletePreview(data: unknown): DeletePreview | null {
  const preview = rootValue(data);
  if (!isRecord(preview)) return null;
  const root = toDeletePreviewNode(preview.root);
  if (
    typeof preview.totalDeletedCount !== "number" ||
    typeof preview.hasBlockers !== "boolean" ||
    root === null
  ) {
    return null;
  }
  return {
    totalDeletedCount: preview.totalDeletedCount,
    deleted: toDeletePreviewGroups(preview.deleted),
    updated: toDeletePreviewGroups(preview.updated),
    blocked: toDeletePreviewGroups(preview.blocked),
    hasBlockers: preview.hasBlockers,
    root,
  };
}

/** Narrow a response `pageInfo` to the declared offset shape. */
function toPageInfo(value: unknown): PageInfo | undefined {
  if (!isRecord(value)) return undefined;
  return {
    offset: typeof value.offset === "number" ? value.offset : 0,
    limit: typeof value.limit === "number" ? value.limit : null,
  };
}

function toDeletePreviewGroups(value: unknown): DeletePreviewGroup[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((group) =>
    isRecord(group) && typeof group.label === "string" && typeof group.count === "number"
      ? [{ label: group.label, count: group.count }]
      : [],
  );
}

function toDeletePreviewNode(value: unknown): DeletePreviewNode | null {
  if (
    !isRecord(value) ||
    typeof value.label !== "string" ||
    typeof value.objectLabel !== "string" ||
    (value.objectId !== null &&
      value.objectId !== undefined &&
      typeof value.objectId !== "string")
  ) {
    return null;
  }
  return {
    label: value.label,
    objectLabel: value.objectLabel,
    objectId: value.objectId ?? null,
    children: Array.isArray(value.children)
      ? value.children.flatMap((child) => {
          const node = toDeletePreviewNode(child);
          return node ? [node] : [];
        })
      : [],
  };
}
