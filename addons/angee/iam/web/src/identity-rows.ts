import type {
  IAMGrant,
  IAMRole,
} from "./documents";

export interface IAMRoleRow extends Record<string, unknown> {
  id: string;
  namespace: string;
  label: string;
}

export function roleRows(roles: readonly IAMRole[]): IAMRoleRow[] {
  return [...roles]
    .sort((left, right) =>
      left.namespace.localeCompare(right.namespace)
      || left.label.localeCompare(right.label),
    )
    .map((role) => ({
      id: role.id,
      namespace: role.namespace,
      label: role.label,
    }));
}

export function roleRef(role: Pick<IAMRole, "id" | "namespace">): string {
  return `${role.namespace}/role:${role.id}`;
}

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
