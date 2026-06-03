import type {
  IAMGrant,
  IAMRelationship,
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
  principalId: string;
  principalType: string;
  principalRef: string;
  principalLabel: string;
  role: string;
  namespace: string;
  roleName: string;
}

export interface IAMRelationshipRow extends Record<string, unknown> {
  id: string;
  resourceType: string;
  resourceId: string;
  relation: string;
  subjectType: string;
  subjectId: string;
  subjectRelation: string;
  caveatName: string;
  resourceRef: string;
  subjectRef: string;
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
        principalId: grant.principalId,
        principalType: grant.principalType,
        principalRef: principal,
        principalLabel: grant.principalLabel || principal,
        role: grant.role,
        namespace: roleNamespace(grant.role),
        roleName: roleName(grant.role),
      };
    });
}

export function relationshipRows(
  relationships: readonly IAMRelationship[],
): IAMRelationshipRow[] {
  return [...relationships]
    .sort((left, right) =>
      left.resourceType.localeCompare(right.resourceType)
      || left.resourceId.localeCompare(right.resourceId)
      || left.relation.localeCompare(right.relation)
      || left.subjectType.localeCompare(right.subjectType)
      || left.subjectId.localeCompare(right.subjectId),
    )
    .map((relationship) => {
      const resourceRef = relationshipResourceRef(relationship);
      const subjectRef = relationshipSubjectRef(relationship);
      return {
        id: `${resourceRef}->${subjectRef}:${relationship.caveatName}`,
        resourceType: relationship.resourceType,
        resourceId: relationship.resourceId,
        relation: relationship.relation,
        subjectType: relationship.subjectType,
        subjectId: relationship.subjectId,
        subjectRelation: relationship.subjectRelation,
        caveatName: relationship.caveatName,
        resourceRef,
        subjectRef,
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
  return `${grant.principalType}:${grant.principalId}`;
}

function relationshipResourceRef(relationship: IAMRelationship): string {
  return `${relationship.resourceType}:${relationship.resourceId}#${relationship.relation}`;
}

function relationshipSubjectRef(relationship: IAMRelationship): string {
  const ref = `${relationship.subjectType}:${relationship.subjectId}`;
  return relationship.subjectRelation
    ? `${ref}#${relationship.subjectRelation}`
    : ref;
}
