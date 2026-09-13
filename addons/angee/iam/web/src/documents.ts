// Console-schema operations for the IAM admin surface: the identity overview,
// users, roles, grants, relationships, the REBAC schema, and the grant/revoke
// writes. These root fields live in the `console` runtime schema, so this file is
// globbed against it by the per-schema codegen. The unauthenticated login surface
// (available connections, login start/complete) lives in `./documents.public`.

import { graphql, type DocumentType } from "@angee/gql/console";
import type { DocumentVariables } from "@angee/refine";

export const RecordAccessDocument = graphql(`
  query RecordAccess($targetType: String!, $targetIds: [ID!]!) {
    record_access(target_type: $targetType, target_ids: $targetIds) {
      target_id relation subject subject_type label
    }
    record_access_options(target_type: $targetType, target_ids: $targetIds) {
      relation permission
    }
  }
`);

export const IamOverview = graphql(`
  query IamOverview($peekLimit: Int = 6) {
    iam_roles(limit: 1000, order_by: [{ namespace: asc }, { role_id: asc }]) {
      id
      role_id
      namespace
      label
      declared
      grantable
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
        id
        subject_id
        subject_type
        subject
        subject_relation
        subject_label
        role
        role_name
        namespace
        caveat_name
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

export const IamAssignmentSubjects = graphql(`
  query IamAssignmentSubjects($limit: Int = 500) {
    users(limit: $limit, order_by: [{ username: asc }]) {
      id
      username
      first_name
      last_name
      email
      display_name
      is_active
      assignment_subject
    }
    users_aggregate {
      aggregate { count }
    }
    groups(limit: $limit, order_by: [{ name: asc }]) {
      id
      name
      assignment_subject
    }
    groups_aggregate {
      aggregate { count }
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

// Role writes change both computed REBAC resource projections; keep the blast
// radius beside the verbs that own it.
export const IAM_ROLE_MUTATION_INVALIDATES = ["iam.Grant", "iam.Relationship", "iam.Group", "iam.Role"] as const;

export const IamRevokeRole = graphql(`
  mutation IamRevokeRole($subject: String!, $role: String!, $caveat_name: String! = "") {
    revoke_role(subject: $subject, role: $role, caveat_name: $caveat_name)
  }
`);

export const IamGrantRole = graphql(`
  mutation IamGrantRole($subject: String!, $role: String!) {
    grant_role(subject: $subject, role: $role)
  }
`);

export const IamGroupAccess = graphql(`
  query IamGroupAccess($id: ID!) {
    groups_by_pk(id: $id) {
      id
      members { id subject subject_type subject_id label caveat_name }
      bindings { id resource resource_type resource_id relation caveat_name target_model target_id }
    }
  }
`);

export const IamAddGroupMember = graphql(`
  mutation IamAddGroupMember($group_id: ID!, $subject: String!, $caveat_name: String! = "") {
    add_group_member(group_id: $group_id, subject: $subject, caveat_name: $caveat_name)
  }
`);

export const IamRemoveGroupMember = graphql(`
  mutation IamRemoveGroupMember($group_id: ID!, $subject: String!, $caveat_name: String! = "") {
    remove_group_member(group_id: $group_id, subject: $subject, caveat_name: $caveat_name)
  }
`);

export const IAM_GROUP_MUTATION_INVALIDATES = ["iam.Group", "iam.Grant", "iam.Relationship", "iam.User"] as const;
export type IAMGroupMember = NonNullable<DocumentType<typeof IamGroupAccess>["groups_by_pk"]>["members"][number];
export type IAMGroupBinding = NonNullable<DocumentType<typeof IamGroupAccess>["groups_by_pk"]>["bindings"][number];

export type IAMOverviewVariables = DocumentVariables<typeof IamOverview>;
export type IAMRole = DocumentType<typeof IamOverview>["iam_roles"][number];

export type IAMUsersVariables = DocumentVariables<typeof IamUsers>;

export type IAMAssignmentSubjectsVariables = DocumentVariables<typeof IamAssignmentSubjects>;

export type IAMAssignmentSubjectsData = DocumentType<typeof IamAssignmentSubjects>;

/** One privileged-grant row, derived from the `IamOverview` selection. The
 * backend `IAMGrantType` computes the full row (`subject`, `role_name`,
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
