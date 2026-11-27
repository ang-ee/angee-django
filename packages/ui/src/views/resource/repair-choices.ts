import type { TypedDocumentNode } from "@angee/refine";
import { gql } from "graphql-tag";

import type { ImplChoice } from "./documents";

// Not in `documents.ts`: the host's codegen loads every `documents.ts` as static documents, and this one is
// built per resource from the root its metadata names.

/** What a resource's repair read answers under its root name. */
export type RepairChoicesResult = Readonly<Record<string, ImplChoice[] | undefined>>;

const repairChoicesDocuments = new Map<string, TypedDocumentNode<RepairChoicesResult, { id: string; field: string }>>();

/**
 * The document of a resource's repair read, `<res>_repair_choices(id, field)`, named by its metadata's
 * `roots.repairChoices`: what choosing each implementation of the create-only selector `field` sets on the
 * record `id` (each choice's `defaults`, the config it carries over), answered under the root's name. One
 * document per root.
 */
export function repairChoicesDocument(root: string): TypedDocumentNode<RepairChoicesResult, { id: string; field: string }> {
  if (!/^[_A-Za-z][_0-9A-Za-z]*$/.test(root)) throw new Error(`Invalid repair read root ${JSON.stringify(root)}.`);
  let document = repairChoicesDocuments.get(root);
  if (!document) {
    document = gql(`
      query BaseImplRepairChoices($id: ID!, $field: String!) {
        ${root}(id: $id, field: $field) {
          key
          category
          defaults
          config_schema
        }
      }
    `) as TypedDocumentNode<RepairChoicesResult, { id: string; field: string }>;
    repairChoicesDocuments.set(root, document);
  }
  return document;
}
