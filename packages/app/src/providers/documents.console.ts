import { parse } from "graphql";
import type { TypedDocumentNode } from "@angee/refine";
import { currentUserSelection, identitySelection, type AngeeCurrentUserData } from "./documents.public";

export interface AngeeViewAsIdentityResult {
  current_user: AngeeCurrentUserData;
  real_user: AngeeCurrentUserData;
}

/** IAM's console-only preview projection; no generated project schema import. */
export const AngeeViewAsIdentityDocument = parse(`
  query AngeeViewAsIdentity($refs: [String!]!) {
    current_user { ${identitySelection} }
    real_user { ${currentUserSelection} }
  }
`) as TypedDocumentNode<AngeeViewAsIdentityResult, { refs: string[] }>;
