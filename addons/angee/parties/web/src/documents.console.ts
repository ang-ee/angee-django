// Bespoke console operations for the parties directories surface: connecting a
// CardDAV source and listing connected directories. Sync is a single-id action
// (`syncIntegration(id): ActionResult`) driven by useActionMutation at the call
// site, so no document is authored for it here.

import { graphql, type DocumentType } from "@angee/gql/console";

export const ConnectCardDavDirectory = graphql(`
  mutation ConnectCardDavDirectory(
    $name: String!
    $url: String!
    $username: String!
    $password: String!
  ) {
    connectCardDavDirectory(name: $name, url: $url, username: $username, password: $password) {
      id
      status
      config
    }
  }
`);

export const PartiesDirectories = graphql(`
  query PartiesDirectories($pagination: OffsetPaginationInput) {
    directories(pagination: $pagination) {
      results {
        id
        status
        backendClass
        config
        lastSyncStatus
        lastSyncCompletedAt
        lastSyncItems
      }
    }
  }
`);

/** One row of the connected-directories list. */
export type DirectoryRow = DocumentType<typeof PartiesDirectories>["directories"]["results"][number];
