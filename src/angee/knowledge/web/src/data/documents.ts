// Scoped, paged resource reads and authored Knowledge verbs.

import { graphql, type DocumentType } from "@angee/gql/console";

export const PAGE_MODEL = "knowledge.Page";
export const MARKDOWN_PAGE_MODEL = "knowledge.MarkdownPage";
export const RECORD_BINDING_MODEL = "knowledge.RecordBinding";
export const PAGE_READ_MODELS = [PAGE_MODEL, MARKDOWN_PAGE_MODEL] as const;

export const KnowledgeCreatePage = graphql(`
  mutation KnowledgeCreatePage($vault: ID!, $title: String!, $kind: String!, $parent: ID) {
    create_page(vault: $vault, title: $title, kind: $kind, parent: $parent) { id }
  }
`);

export const KnowledgeCreateVaultFrom = graphql(`
  mutation KnowledgeCreateVaultFrom(
    $template: ID!
    $name: String!
    $owned: Boolean! = true
    $idempotency_key: String
  ) {
    create_vault_from(
      template: $template
      name: $name
      owned: $owned
      idempotency_key: $idempotency_key
    ) {
      ok
      message
      validation_errors
      id
    }
  }
`);

export const KnowledgeUpdatePageBody = graphql(`
  mutation KnowledgeUpdatePageBody($page: ID!, $body: String!, $expected_hash: String, $title: String) {
    update_page_body(page: $page, body: $body, expected_hash: $expected_hash, title: $title) {
      ok
      error
      error_code
      markdown {
        title
        body
        body_hash
        word_count
      }
    }
  }
`);

export const KnowledgeVaults = graphql(`
  query KnowledgeVaults($limit: Int, $offset: Int, $where: vaults_bool_exp) {
    vaults(where: $where, order_by: [{ name: asc }], limit: $limit, offset: $offset) {
      id
      name
      description
      icon
      accent
      permissions
    }
  }
`);

export const KnowledgeVault = graphql(`
  query KnowledgeVault($id: String!) {
    vaults_by_pk(id: $id) { id name permissions }
  }
`);

/** Resolve one declared resource handle; display names never select a write destination. */
export const KnowledgeVaultForResource = graphql(`
  query KnowledgeVaultForResource($reference: String!) {
    knowledge_vault_for_resource(reference: $reference) { id permissions }
  }
`);

export const KnowledgePages = graphql(`
  query KnowledgePages($limit: Int, $offset: Int, $where: pages_bool_exp, $order: [pages_order_by!]) {
    pages(where: $where, order_by: $order, limit: $limit, offset: $offset) {
      id
      permissions
      title
      kind
      icon
      vault
      vault_label
      parent
      has_children
      updated_at
      created_by_label
      is_trashed
      trash_reason
      trashed_by_label
    }
    pages_aggregate(where: $where) { aggregate { count } }
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

/** The records one page is bound to; shared reference hooks resolve their labels and routes. */
export const KnowledgePageRecordBindings = graphql(`
  query KnowledgePageRecordBindings($page: ID!) {
    page_record_bindings(page: $page) {
      id
      role
      model_label
      record_id
    }
  }
`);

export const KnowledgeRecordNotes = graphql(`
  query KnowledgeRecordNotes($modelLabel: String!, $recordId: ID!, $role: String!) {
    record_knowledge_bindings(model_label: $modelLabel, record_id: $recordId, role: $role) {
      page_detail {
        id
        title
        created_at
        created_by_label
        markdown { body }
      }
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

export const KnowledgeCreateRecordNote = graphql(`
  mutation KnowledgeCreateRecordNote($model_label: String!, $record_id: ID!, $vault: ID!, $body: String!, $role: String!, $idempotency_key: String!) {
    create_record_note(model_label: $model_label, record_id: $record_id, vault: $vault, body: $body, role: $role, idempotency_key: $idempotency_key) { id page }
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
      vault_label
      parent
      has_children
      is_trashed
      updated_at
      created_by_label
      ancestors { id title parent kind icon has_children permissions }
      wikilinks { title page }
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
