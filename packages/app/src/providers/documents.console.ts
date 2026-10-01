import { parse } from "graphql";
import type { TypedDocumentNode } from "@angee/refine";
import { currentUserSelection, type AngeeCurrentUserData } from "./documents.public";

export interface AngeeViewAsIdentityResult {
  current_user: AngeeCurrentUserData;
  real_user: AngeeCurrentUserData;
  viewable_people: { id: string; name: string }[];
}

/** IAM's console-only preview projection; no generated project schema import. */
export const AngeeViewAsIdentityDocument = parse(`
  query AngeeViewAsIdentity {
    current_user { ${currentUserSelection} }
    real_user { ${currentUserSelection} }
    viewable_people { id name: display_name }
  }
`) as TypedDocumentNode<AngeeViewAsIdentityResult, Record<string, never>>;
