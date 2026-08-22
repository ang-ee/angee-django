import { graphql, type DocumentType } from "@angee/gql/public";

export const AngeeCurrentUserDocument = graphql(`
  query AngeeCurrentUser {
    current_user {
      id
      username
      firstName: first_name
      lastName: last_name
      email
      isStaff: is_staff
      isActive: is_active
      preferences
      roleRefs: role_refs
    }
  }
`);

export const AngeeLoginDocument = graphql(`
  mutation AngeeLogin($username: String!, $password: String!) {
    login(username: $username, password: $password) {
      ok
      user {
        id
        username
        firstName: first_name
        lastName: last_name
        email
        isStaff: is_staff
        isActive: is_active
        preferences
      }
    }
  }
`);

export const AngeeLogoutDocument = graphql(`
  mutation AngeeLogout {
    logout
  }
`);

export const AngeeUpdatePreferencesDocument = graphql(`
  mutation AngeeUpdatePreferences($preferences: JSON!) {
    update_preferences(preferences: $preferences) {
      id
      username
      firstName: first_name
      lastName: last_name
      email
      isStaff: is_staff
      isActive: is_active
      preferences
      roleRefs: role_refs
    }
  }
`);

export type AngeeCurrentUserData = DocumentType<
  typeof AngeeCurrentUserDocument
>["current_user"];

export type AngeeLoginUserData = DocumentType<
  typeof AngeeLoginDocument
>["login"]["user"];
