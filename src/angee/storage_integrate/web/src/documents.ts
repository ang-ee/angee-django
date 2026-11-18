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

export const MountBackendFacts = graphql(`
  query MountBackendFacts($backendClass: String!) {
    mount_backend(backend_class: $backendClass) {
      key
      requires_credential
      ...ConnectWithAccountFields
    }
  }
`);

export const MountCopyTargets = graphql(`
  query MountCopyTargets {
    backends {
      id
      label
      writable
      is_archived
    }
  }
`);

export const ConnectMount = graphql(`
  mutation ConnectMount(
    $backendClass: String!
    $name: String!
    $root: String!
    $mode: MountMode!
    $credentialId: ID
    $copyBackendId: ID
  ) {
    connect_mount(
      backend_class: $backendClass
      name: $name
      source_root: $root
      mode: $mode
      credential_id: $credentialId
      copy_backend_id: $copyBackendId
    ) {
      id
      display_name
      mode
      lifecycle
      runtime_status
      drive
    }
  }
`);
