// Relation fields (`page.vault`, `page.parent`) come back as the bare public sqid,
// but a node `id` is a base64 relay GlobalID. Normalise bare relation ids up so
// client-side joins (page → parent, page → vault) match. The codec is SDK-owned
// (one relay boundary); only the per-addon type constants live here.
export {
  toRelayGlobalId as toGlobalId,
  relationRelayGlobalId as relationGlobalId,
} from "@angee/sdk";

export const VAULT_TYPE = "VaultType";
export const PAGE_TYPE = "PageType";
