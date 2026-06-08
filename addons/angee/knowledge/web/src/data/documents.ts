// Authored GraphQL for the knowledge wiki. Vaults and pages are read through
// their offset-paginated connections (fetched once; the browser scopes to the
// active vault client-side, see `page-rows.ts`); the open page's body and
// backlinks load on demand through the relay node query.

// A `type` alias (not an `interface`) so it carries an implicit index signature
// and satisfies the authored-hook `Variables` (`Record<string, unknown>`) bound.
export type OffsetPaginationVariables = {
  pagination: { offset: number; limit: number };
};

export type PageIdVariables = { id: string };

/** A vault — the access-control boundary a tree of pages lives in. */
export interface KnowledgeVault {
  id: string;
  name: string;
  description: string;
  icon: string;
  accent: string;
}

/** A page projected for the tree. `vault`/`parent` are the parents' public ids. */
export interface KnowledgePage {
  id: string;
  title: string;
  kind: string;
  icon: string;
  vault: string;
  parent: string | null;
  updatedAt: string;
  createdByLabel: string | null;
}

/** One resolved page that links to the page being viewed. */
export interface Backlink {
  page: string;
  title: string;
  displayText: string;
}

/** The open page's full record — its markdown body and backlinks. */
export interface KnowledgePageDetail extends KnowledgePage {
  markdown: { body: string; bodyHash: string; wordCount: number } | null;
  backlinks: readonly Backlink[];
}

export type UpdatePageVariables = {
  data: { id: string; title?: string; parent?: string | null };
};
export interface UpdatePageData {
  updatePage: { id: string; title: string };
}

export type CreatePageVariables = {
  data: { vault: string; title: string; kind?: string; parent?: string | null };
};
export interface CreatePageData {
  createPage: { id: string; title: string };
}

export type CreateVaultVariables = { data: { name: string } };
export interface CreateVaultData {
  createVault: { id: string; name: string };
}

export type DeletePageVariables = { id: string };
export interface DeletePageData {
  deletePage: { totalDeletedCount: number; hasBlockers: boolean };
}

export const CREATE_PAGE_MUTATION = `
  mutation KnowledgeCreatePage($data: PageInput!) {
    createPage(data: $data) {
      id
      title
    }
  }
`;

export const CREATE_VAULT_MUTATION = `
  mutation KnowledgeCreateVault($data: VaultInput!) {
    createVault(data: $data) {
      id
      name
    }
  }
`;

export const DELETE_PAGE_MUTATION = `
  mutation KnowledgeDeletePage($id: ID!) {
    deletePage(id: $id, confirm: true) {
      totalDeletedCount
      hasBlockers
    }
  }
`;

export type UpdatePageBodyVariables = {
  page: string;
  body: string;
  expectedHash?: string | null;
};
export interface UpdatePageBodyData {
  updatePageBody: {
    ok: boolean;
    errorCode: string | null;
    markdown: { body: string; bodyHash: string; wordCount: number } | null;
  };
}

export const UPDATE_PAGE_MUTATION = `
  mutation KnowledgeUpdatePage($data: PagePatch!) {
    updatePage(data: $data) {
      id
      title
    }
  }
`;

export const UPDATE_PAGE_BODY_MUTATION = `
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
`;

export interface KnowledgeVaultsData {
  vaults: { results: KnowledgeVault[] };
}

export interface KnowledgePagesData {
  pages: { results: KnowledgePage[] };
}

export interface KnowledgePageData {
  page: KnowledgePageDetail | null;
}

export const KNOWLEDGE_VAULTS_QUERY = `
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
`;

export const KNOWLEDGE_PAGES_QUERY = `
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
`;

export const KNOWLEDGE_PAGE_QUERY = `
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
`;
