import { graphql } from "@angee/gql/console";

export const ConnectGoogleDrive = graphql(`
  mutation ConnectGoogleDrive(
    $name: String!
    $credentialId: ID
    $path: String!
    $mode: MountMode!
    $copyBackendId: ID
  ) {
    connect_google_drive(
      name: $name
      credential_id: $credentialId
      path: $path
      mode: $mode
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
