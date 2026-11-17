import { graphql, type DocumentType } from "@angee/gql/console";

/** A record's checklist as its reader sees it; select it on any node composing `ChecklistNode`. */
export const ChecklistFragment = graphql(`
  fragment ChecklistFields on ChecklistAssessment {
    status detail complete stale recorded_at
    groups { key label status detail item_keys }
    items { key label category blocking status detail stale recorded_at evidence { model id } }
  }
`);

export type ChecklistFields = DocumentType<typeof ChecklistFragment>;
