import type { ResourceLedgerRowData } from "../documents";

export interface ResourceRow extends Record<string, unknown> {
  id: string;
  source: string;
  path: string;
  tier: string;
  target: string;
  targetId: string;
  hash: string;
  loaded: string;
}

export function resourceRows(
  ledger: readonly ResourceLedgerRowData[],
): ResourceRow[] {
  return ledger.map((row) => ({
    id: row.id,
    source: row.sourceAddon,
    path: row.sourcePath,
    tier: row.tier,
    target: row.targetModel,
    targetId: row.targetId,
    hash: row.contentHash.slice(0, 12),
    loaded: row.loadedAt ? row.loadedAt.slice(0, 19).replace("T", " ") : "",
  }));
}
