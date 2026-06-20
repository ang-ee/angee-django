// Authored GraphQL for the knowledge wiki. Vaults and pages are read through
// their offset-paginated connections (fetched once; the browser scopes to the
// active vault client-side, see `page-rows.ts`); the open page's body and
// backlinks load on demand through the public detail query. Standard CRUD
// mutations are emitted by the SDK; only markdown/body-specific writes are
// authored here.

import { graphql, type DocumentType } from "@angee/gql/console";

export const KnowledgeUpdatePageBody = graphql(`
  mutation KnowledgeUpdatePageBody($page: ID!, $body: String!, $expectedHash: String) {
    updatePageBody(page: $page, body: $body, expectedHash: $expectedHash) {
      ok
      errorCode
      markdown {
        body
        bodyHash
        wordCount
      }
    }
  }
`);

export const KnowledgeVaults = graphql(`
  query KnowledgeVaults($pagination: OffsetPaginationInput) {
    vaults(pagination: $pagination) {
      results {
        id
        name
        description
        icon
        accent
      }
    }
  }
`);

export const KnowledgePages = graphql(`
  query KnowledgePages($pagination: OffsetPaginationInput) {
    pages(pagination: $pagination) {
      results {
        id
        title
        kind
        icon
        vault
        parent
        updatedAt
        createdByLabel
      }
    }
  }
`);

export const KnowledgePage = graphql(`
  query KnowledgePage($id: ID!) {
    page(id: $id) {
      id
      title
      kind
      icon
      vault
      parent
      updatedAt
      createdByLabel
      markdown {
        body
        bodyHash
        wordCount
      }
      backlinks {
        page
        title
        displayText
      }
    }
  }
`);

/** A page projected for the tree, derived from the `KnowledgePages` result. */
export type KnowledgePageRow =
  DocumentType<typeof KnowledgePages>["pages"]["results"][number];

/** The open page's full record — its markdown body and backlinks. */
export type KnowledgePageDetail = NonNullable<
  DocumentType<typeof KnowledgePage>["page"]
>;

/** One resolved page that links to the page being viewed. */
export type Backlink = KnowledgePageDetail["backlinks"][number];
