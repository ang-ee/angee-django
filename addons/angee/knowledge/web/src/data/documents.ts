// Authored GraphQL for the knowledge wiki. Vaults and pages are read through
// Hasura-shaped resources (fetched once; the browser scopes to the active vault
// client-side, see `page-rows.ts`); the open page's body and backlinks load on
// demand through the public detail query. Standard CRUD mutations are emitted
// by the SDK; only markdown/body-specific writes are authored here.

import { graphql, type DocumentType } from "@angee/gql/console";

export const KNOWLEDGE_LIST_LIMIT = 500;

export const PAGE_MODEL = "knowledge.Page";
export const MARKDOWN_PAGE_MODEL = "knowledge.MarkdownPage";
export const PAGE_READ_MODELS = [PAGE_MODEL, MARKDOWN_PAGE_MODEL] as const;

export const KnowledgeCreateVaultFrom = graphql(`
  mutation KnowledgeCreateVaultFrom(
    $template: ID!
    $name: String!
    $owned: Boolean! = true
    $client_creation_key: String
  ) {
    create_vault_from(
      template: $template
      name: $name
      owned: $owned
      client_creation_key: $client_creation_key
    ) {
      ok
      message
      validation_errors
      id
    }
  }
`);

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
      permissions
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

export const KnowledgeRecordPages = graphql(`
  query KnowledgeRecordPages($modelLabel: String!, $recordId: ID!, $role: String) {
    record_knowledge_bindings(model_label: $modelLabel, record_id: $recordId, role: $role) {
      id
      role
      page
      page_title
      page_can_write
    }
    record_knowledge_can_bind(model_label: $modelLabel, record_id: $recordId)
  }
`);

export const KnowledgeBindRecord = graphql(`
  mutation KnowledgeBindRecord($input: RecordBindingInput!) {
    bind_knowledge_record(input: $input) { id page role }
  }
`);

export const KnowledgeUnbindRecord = graphql(`
  mutation KnowledgeUnbindRecord($input: RecordBindingInput!) {
    unbind_knowledge_record(input: $input)
  }
`);

export const KnowledgePage = graphql(`
  query KnowledgePage($id: String!) {
    pages_by_pk(id: $id) {
      id
      permissions
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
