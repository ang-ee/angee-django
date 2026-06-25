import type {
  IAMGrant,
  IAMRole,
} from "./documents";

export interface IAMRoleRow extends Record<string, unknown> {
  id: string;
  namespace: string;
  label: string;
  description: string;
}

export interface IAMGrantRow extends Record<string, unknown> {
  id: string;
  principal_id: string;
  principal_type: string;
  principalRef: string;
  principal_label: string;
  role: string;
  namespace: string;
  roleName: string;
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
      description: role.description,
    }));
}

export function roleRef(role: Pick<IAMRole, "id" | "namespace">): string {
  return `${role.namespace}/role:${role.id}`;
}

export function grantRows(grants: readonly IAMGrant[]): IAMGrantRow[] {
  return [...grants]
    .sort((left, right) =>
      roleNamespace(left.role).localeCompare(roleNamespace(right.role))
      || left.role.localeCompare(right.role)
      || principalRef(left).localeCompare(principalRef(right)),
    )
    .map((grant) => {
      const principal = principalRef(grant);
      return {
        id: `${principal}:${grant.role}`,
        principal_id: grant.principal_id,
        principal_type: grant.principal_type,
        principalRef: principal,
        principal_label: grant.principal_label || principal,
        role: grant.role,
        namespace: roleNamespace(grant.role),
        roleName: roleName(grant.role),
      };
    });
}

export function roleNamespace(role: string): string {
  const slash = role.indexOf("/");
  return slash > 0 ? role.slice(0, slash) : "default";
}

function roleName(role: string): string {
  const colon = role.lastIndexOf(":");
  return colon >= 0 ? role.slice(colon + 1) : role;
}

function principalRef(grant: IAMGrant): string {
  return `${grant.principal_type}:${grant.principal_id}`;
}
