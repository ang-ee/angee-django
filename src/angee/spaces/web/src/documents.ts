import { graphql } from "@angee/gql/console";

export const SPACE_MEMBERSHIP_INVALIDATES = [
  "spaces.Membership",
  "spaces.Group",
] as const;

export const AddSpaceMembership = graphql(`
  mutation AddSpaceMembership($group: ID!, $party: ID!, $role: MembershipRole!) {
    add_space_membership(group_id: $group, party_id: $party, role: $role) {
      id
      role
      is_confirmed
    }
  }
`);

export const UpdateSpaceMembershipRole = graphql(`
  mutation UpdateSpaceMembershipRole($id: String!, $role: String!) {
    update_space_memberships_by_pk(
      pk_columns: { id: $id }
      _set: { role: $role }
    ) {
      id
      role
    }
  }
`);

export const RemoveSpaceMembership = graphql(`
  mutation RemoveSpaceMembership($id: String!) {
    delete_space_memberships_by_pk(id: $id) {
      id
    }
  }
`);

export const SetMembershipNotifications = graphql(`
  mutation SetMembershipNotifications($id: ID!, $policy: NotificationPolicy!, $subtype_keys: [String!]!) {
    set_membership_notifications(id: $id, policy: $policy, subtype_keys: $subtype_keys) {
      id
      notification_policy
      subtype_keys
    }
  }
`);
