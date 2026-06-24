// Authored GraphQL for the knowledge wiki. Vaults and pages are read through
// Hasura-shaped resources (fetched once; the browser scopes to the active vault
// client-side, see `page-rows.ts`); the open page's body and backlinks load on
// demand through the public detail query. Standard CRUD mutations are emitted
// by the SDK; only markdown/body-specific writes are authored here.

import { graphql, type DocumentType } from "@angee/gql/console";

export const KnowledgeUpdatePageBody = graphql(`
  mutation KnowledgeUpdatePageBody($page: ID!, $body: String!, $expected_hash: String) {
    update_page_body(page: $page, body: $body, expected_hash: $expected_hash) {
      ok
      error_code
      markdown {
        body
        body_hash
        word_count
      }
    }
  }
`);

export const KnowledgeVaults = graphql(`
  query KnowledgeVaults($limit: Int, $offset: Int) {
    vaults(limit: $limit, offset: $offset) {
      id
      name
      description
      icon
      accent
    }
  }
`);

export const KnowledgePages = graphql(`
  query KnowledgePages($limit: Int, $offset: Int) {
    pages(limit: $limit, offset: $offset) {
      id
      title
      kind
      icon
      vault
      parent
      updated_at
      created_by_label
    }
  }
`);

export const KnowledgePage = graphql(`
  query KnowledgePage($id: String!) {
    pages_by_pk(id: $id) {
      id
      title
      kind
      icon
      vault
      parent
      updated_at
      created_by_label
      markdown {
        body
        body_hash
        word_count
      }
      backlinks {
        page
        title
        display_text
      }
    }
  }
`);

/** A page projected for the tree, derived from the `KnowledgePages` result. */
export type KnowledgePageRow =
  NonNullable<DocumentType<typeof KnowledgePages>["pages"]>[number];

/** The open page's full record — its markdown body and backlinks. */
export type KnowledgePageDetail = NonNullable<
  DocumentType<typeof KnowledgePage>["pages_by_pk"]
>;

/** One resolved page that links to the page being viewed. */
export type Backlink = KnowledgePageDetail["backlinks"][number];
