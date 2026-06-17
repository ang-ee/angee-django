// Hand-authored console query for the resource import ledger. The resources
// backend owns the schema (`addons/angee/resources/schema.py`); this string and
// its types mirror it, the same no-codegen pattern IAM uses.

export const RESOURCE_LEDGER_QUERY = `
  query ResourceLedger {
    resourceLedger {
      id
      sourceAddon
      sourcePath
      tier
      contentHash
      targetModel
      targetId
      loadedAt
    }
  }
`;

export interface ResourceLedgerRowData {
  id: string;
  sourceAddon: string;
  sourcePath: string;
  tier: string;
  contentHash: string;
  targetModel: string;
  targetId: string;
  loadedAt: string;
}

export interface ResourceLedgerResult {
  resourceLedger: readonly ResourceLedgerRowData[];
}
