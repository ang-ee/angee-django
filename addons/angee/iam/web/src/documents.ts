// Console-schema operations for the IAM admin surface: the identity overview,
// users, roles, grants, relationships, the REBAC schema, and the grant/revoke
// writes. These root fields live in the `console` runtime schema, so this file is
// globbed against it by the per-schema codegen. The unauthenticated login surface
// (available connections, login start/complete) lives in `./documents.public`.

import { graphql, type DocumentType } from "@angee/gql/console";
import type { DocumentVariables } from "@angee/sdk";

export const IamRoles = graphql(`
  query IamRoles {
    roles {
      id
      namespace
      label
      description
    }
  }
`);

export const IamOverview = graphql(`
  query IamOverview($pagination: OffsetPaginationInput) {
    users(pagination: $pagination) {
      totalCount
    }
    roles {
      id
      namespace
      label
      description
    }
    grants(pagination: $pagination) {
      totalCount
    }
    relationships(pagination: $pagination) {
      totalCount
    }
  }
`);

export const IamUsers = graphql(`
  query IamUsers($pagination: OffsetPaginationInput) {
    users(pagination: $pagination) {
      totalCount
      results {
        id
        username
        firstName
        lastName
        email
        isStaff
        isActive
      }
    }
  }
`);

export const IamGrants = graphql(`
  query IamGrants($pagination: OffsetPaginationInput) {
    grants(pagination: $pagination) {
      totalCount
      results {
        principalId
        principalType
        principalLabel
        role
      }
    }
  }
`);

export const IamRelationships = graphql(`
  query IamRelationships(
    $resourceType: String
    $subjectType: String
    $relation: String
    $pagination: OffsetPaginationInput
  ) {
    relationships(
      resourceType: $resourceType
      subjectType: $subjectType
      relation: $relation
      pagination: $pagination
    ) {
      totalCount
      results {
        resourceType
        resourceId
        relation
        subjectType
        subjectId
        subjectRelation
        caveatName
      }
    }
  }
`);

export const IamRebacSchema = graphql(`
  query IamRebacSchema {
    rebacSchema {
      resourceType
      relations {
        name
        allowedSubjectTypes
      }
      permissions {
        name
        conditions {
          name
        }
      }
    }
  }
`);

export const IamRevokeRole = graphql(`
  mutation IamRevokeRole($principalId: String!, $role: String!) {
    revokeRole(principalId: $principalId, role: $role)
  }
`);

export const IamGrantRole = graphql(`
  mutation IamGrantRole($principalId: String!, $role: String!) {
    grantRole(principalId: $principalId, role: $role)
  }
`);

/** One `roles` row, derived from the `IamRoles` selection. */
export type IAMRole = DocumentType<typeof IamRoles>["roles"][number];

export type IAMOverviewVariables = DocumentVariables<typeof IamOverview>;

export type IAMUsersVariables = DocumentVariables<typeof IamUsers>;

/** One `grants.results` row, derived from the `IamGrants` selection. */
export type IAMGrant = DocumentType<typeof IamGrants>["grants"]["results"][number];

export type IAMGrantsVariables = DocumentVariables<typeof IamGrants>;

/** One `relationships.results` row, derived from the `IamRelationships` selection. */
export type IAMRelationship =
  DocumentType<typeof IamRelationships>["relationships"]["results"][number];

export type IAMRelationshipsVariables = DocumentVariables<typeof IamRelationships>;

/** One `rebacSchema` resource entry, derived from the `IamRebacSchema` selection. */
export type IAMResourceSchema =
  DocumentType<typeof IamRebacSchema>["rebacSchema"][number];

/** A relation within a `rebacSchema` resource. */
export type IAMRelationSchema = IAMResourceSchema["relations"][number];

/** A permission within a `rebacSchema` resource. */
export type IAMPermissionSchema = IAMResourceSchema["permissions"][number];

export type IAMRevokeRoleVariables = DocumentVariables<typeof IamRevokeRole>;

export type IAMGrantRoleVariables = DocumentVariables<typeof IamGrantRole>;
