import { graphql } from "@angee/gql/console";

export const MOUNT_MODEL = "storage_integrate.Mount";

export const BrowseMountSource = graphql(`
  query BrowseMountSource($backendClass: String, $credentialId: ID, $token: String) {
    browse_mount_source(
      backend_class: $backendClass
      credential_id: $credentialId
      token: $token
    ) {
      location {
        token
        label
        is_navigable
        is_mountable
        blocked_reason
      }
      parent_token
      truncated
      supports_manual_token
      entries {
        token
        label
        is_navigable
        is_mountable
        blocked_reason
      }
    }
  }
`);

export const MountAccounts = graphql(`
  query MountAccounts($backendClass: String!) {
    mount_accounts(backend_class: $backendClass) {
      oauth_client
      accounts {
        id
        display_name
      }
    }
  }
`);

export const ConnectLocalFolder = graphql(`
  mutation ConnectLocalFolder($name: String!, $path: String!, $mode: MountMode!, $copyBackendId: ID) {
    connect_local_folder(name: $name, path: $path, mode: $mode, copy_backend_id: $copyBackendId) {
      id
      display_name
      mode
      lifecycle
      runtime_status
      drive
    }
  }
`);
