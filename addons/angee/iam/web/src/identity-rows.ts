import type {
  IAMGrant,
} from "./documents";

/** Stable sort of the overview's privileged-grant peek. Every field is computed
 * on the backend (`IAMGrantType`); this only orders the rows. The `principal_ref`
 * and `role` the backend feeds compose the row key, so no parsing happens here. */
export function grantRows(grants: readonly IAMGrant[]): readonly IAMGrant[] {
  return [...grants].sort((left, right) =>
    left.namespace.localeCompare(right.namespace)
    || left.role.localeCompare(right.role)
    || left.principal_ref.localeCompare(right.principal_ref),
  );
}
