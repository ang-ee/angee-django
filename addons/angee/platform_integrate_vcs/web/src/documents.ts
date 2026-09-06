import { graphql, type DocumentType } from "@angee/gql/console";

export const AddAddonSource = graphql(`
  mutation AddAddonSource($data: AddonSourceInput!) {
    add_source(data: $data) { ok message }
  }
`);

export const ScanAddonSource = graphql(`
  mutation ScanAddonSource($sourceId: ID!) {
    scan(source_id: $sourceId) { ok message }
  }
`);

export const AddonSources = graphql(`
  query AddonSources {
    sources(where: { kind: { _eq: "addon" } }, order_by: [{ updated_at: desc }]) {
      id display_name kind ref path
    }
  }
`);

export type AddonSourceRow = DocumentType<typeof AddonSources>["sources"][number];
