export type Row = Record<string, unknown>;

/** The public record id carried by resource rows, or null for non-record values. */
export function rowPublicId(
  row: Row | null | undefined,
  resource?: { publicIdField?: string | null } | null,
): string | null {
  const field = resource?.publicIdField || "id";
  const value = row?.[field];
  return typeof value === "string" ? value : null;
}

export function publicIdLabel(value: string): string | null {
  const publicId = value.trim();
  return publicId === "" ? null : publicId;
}

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
