import { graphql } from "@angee/gql/console";

// Telegram owns channel creation; pairing and lifecycle actions are generic.
export const ConnectTelegramChannel = graphql(`
  mutation ConnectTelegramChannel($name: String!, $credentialId: ID!) {
    connect_telegram_channel(name: $name, credential_id: $credentialId) {
      id
      lifecycle
      runtime_status
    }
  }
`);

// The credential resource exposes no create root — its material is secret, so
// `CredentialType` never projects it and auto-CRUD cannot carry it. The picker's
// inline create saves through this instead (see `RelationCreateConfig.submit`).
export const CreateTelegramAppKeys = graphql(`
  mutation CreateTelegramAppKeys(
    $name: String!
    $appId: String!
    $appSecret: String!
  ) {
    create_credential(
      data: {
        name: $name
        kind: "app_keys"
        app_id: $appId
        app_secret: $appSecret
      }
    ) {
      id
      display_name
    }
  }
`);
