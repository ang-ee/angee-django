import { graphql } from "@angee/gql/console";

export const ConnectIphoneBackup = graphql(`
  mutation ConnectIphoneBackup($name: String!, $path: String!, $mode: MountMode!) {
    connect_iphone_backup(name: $name, path: $path, mode: $mode) {
      id
      display_name
      mode
      lifecycle
      runtime_status
      drive
    }
  }
`);
