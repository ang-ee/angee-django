import { formatDateTime } from "@angee/base";

import type { ResourceLedgerRowData } from "../documents";

export interface ResourceRow extends Record<string, unknown> {
  id: string;
  sourceAddon: string;
  sourcePath: string;
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
    sourceAddon: row.sourceAddon,
    sourcePath: row.sourcePath,
    tier: row.tier,
    target: row.targetModel,
    targetId: row.targetId,
    hash: row.contentHash.slice(0, 12),
    loaded: formatDateTime(row.loadedAt),
  }));
}
