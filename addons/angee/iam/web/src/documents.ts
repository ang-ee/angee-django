// Console-schema operations for the IAM admin surface: the identity overview,
// users, roles, grants, relationships, the REBAC schema, and the grant/revoke
// writes. These root fields live in the `console` runtime schema, so this file is
// globbed against it by the per-schema codegen. The unauthenticated login surface
// (available connections, login start/complete) lives in `./documents.public`.

import { graphql, type DocumentType } from "@angee/gql/console";
import type { DocumentVariables } from "@angee/refine";

export const IamOverview = graphql(`
  query IamOverview($peekLimit: Int = 6) {
    roles {
      id
      namespace
      label
    }
    iam_overview(peek_limit: $peekLimit) {
      user_count
      role_count
      grant_count
      relationship_count
      privileged_grant_count
      unassigned_user_count
      namespaces {
        namespace
        role_count
        grant_count
      }
      privileged_grants {
        principal_id
        principal_type
        principal_ref
        principal_label
        role
        role_name
        namespace
      }
      unassigned_users {
        id
        username
        email
      }
    }
  }
`);

export const IamUsers = graphql(`
  query IamUsers($limit: Int = 500, $offset: Int = 0) {
    users(limit: $limit, offset: $offset, order_by: [{ username: asc }]) {
      id
      username
      first_name
      last_name
      email
      is_staff
      is_active
    }
    users_aggregate {
      aggregate {
        count
      }
    }
  }
`);

export const IamRebacSchema = graphql(`
  query IamRebacSchema {
    rebac_schema {
      resource_type
      relations {
        name
        allowed_subject_types
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
  mutation IamRevokeRole($principal_id: String!, $role: String!) {
    revoke_role(principal_id: $principal_id, role: $role)
  }
`);

export const IamGrantRole = graphql(`
  mutation IamGrantRole($principal_id: String!, $role: String!) {
    grant_role(principal_id: $principal_id, role: $role)
  }
`);

/** One `roles` row, derived from the `IamOverview` selection — the same
 * `{id, namespace, label}` shape the dropped standalone `IamRoles` query exposed. */
export type IAMRole = DocumentType<typeof IamOverview>["roles"][number];

export type IAMOverviewVariables = DocumentVariables<typeof IamOverview>;

export type IAMUsersVariables = DocumentVariables<typeof IamUsers>;

/** One privileged-grant row, derived from the `IamOverview` selection. The
 * backend `IAMGrantType` computes the full row (`principal_ref`, `role_name`,
 * `namespace`) via the same helpers as the `iam.Grant` Hasura resource, so the
 * client no longer re-derives any of them. The Grants page reads that resource
 * directly; this type only backs the overview's privileged-grant peek. */
export type IAMGrant = NonNullable<
  DocumentType<typeof IamOverview>["iam_overview"]
>["privileged_grants"][number];

/** One `rebac_schema` resource entry, derived from the `IamRebacSchema` selection. */
export type IAMResourceSchema =
  DocumentType<typeof IamRebacSchema>["rebac_schema"][number];

/** A relation within a `rebac_schema` resource. */
export type IAMRelationSchema = IAMResourceSchema["relations"][number];

/** A permission within a `rebac_schema` resource. */
export type IAMPermissionSchema = IAMResourceSchema["permissions"][number];

export type IAMRevokeRoleVariables = DocumentVariables<typeof IamRevokeRole>;

export type IAMGrantRoleVariables = DocumentVariables<typeof IamGrantRole>;
