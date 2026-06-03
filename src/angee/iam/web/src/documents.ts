export const AVAILABLE_CONNECTIONS_QUERY = `
  query IamAvailableConnections {
    availableConnections {
      results {
        oauthClientSqid
        oauthClientDisplayName
        vendor {
          displayName
        }
      }
    }
  }
`;

export const LOGIN_START_MUTATION = `
  mutation IamLoginStart(
    $oauthClientSqid: String!
    $redirectUri: String!
    $next: String!
  ) {
    loginStart(
      oauthClientSqid: $oauthClientSqid
      redirectUri: $redirectUri
      next: $next
    ) {
      authorizeUrl
      error
    }
  }
`;

export const LOGIN_COMPLETE_MUTATION = `
  mutation IamLoginComplete(
    $code: String!
    $state: String!
    $redirectUri: String!
  ) {
    loginComplete(code: $code, state: $state, redirectUri: $redirectUri) {
      ok
      next
      error
    }
  }
`;

export const IAM_ROLES_QUERY = `
  query IamRoles {
    roles {
      id
      namespace
      label
      description
    }
  }
`;

export const IAM_OVERVIEW_QUERY = `
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
`;

export const IAM_USERS_QUERY = `
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
`;

export const IAM_GRANTS_QUERY = `
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
`;

export const IAM_RELATIONSHIPS_QUERY = `
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
`;

export const IAM_REBAC_SCHEMA_QUERY = `
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
`;

export const IAM_OAUTH_CLIENTS_QUERY = `
  query IamOAuthClients($pagination: OffsetPaginationInput) {
    oauthClients(pagination: $pagination) {
      totalCount
      results {
        id
        displayName
        vendorLabel
        vendorSlug
        environment
        isEnabled
        configurationState
        supportsPkce
      }
    }
  }
`;

export const IAM_CONNECTION_SUMMARY_QUERY = `
  query IamConnectionSummary($pagination: OffsetPaginationInput) {
    vendors(pagination: $pagination) {
      totalCount
      results {
        id
        slug
        displayName
      }
    }
    externalAccounts(pagination: $pagination) {
      totalCount
      results {
        id
        email
        displayName
        status
        credentialStatus
        vendor {
          displayName
        }
      }
    }
    credentialHealth(pagination: $pagination) {
      totalCount
      results {
        id
        kind
        status
        lastRefreshStatus
        oauthClient {
          displayName
        }
        externalAccount {
          email
        }
      }
    }
  }
`;

export const IAM_REVOKE_ROLE_MUTATION = `
  mutation IamRevokeRole($principalId: String!, $role: String!) {
    revokeRole(principalId: $principalId, role: $role)
  }
`;

export const IAM_GRANT_ROLE_MUTATION = `
  mutation IamGrantRole($principalId: String!, $role: String!) {
    grantRole(principalId: $principalId, role: $role)
  }
`;

/** Selection result for SDL `VendorType` in `IamAvailableConnections`. */
export interface AvailableConnectionVendor {
  displayName: string;
}

/** Selection result for an `availableConnections.results` item. */
export interface AvailableConnection {
  oauthClientSqid: string;
  oauthClientDisplayName: string;
  vendor: AvailableConnectionVendor;
}

/** Selection result for `IamAvailableConnections`. */
export interface AvailableConnectionsData {
  availableConnections: {
    results: AvailableConnection[];
  };
}

/** Selection result for SDL `OidcStartPayload` in `IamLoginStart`. */
export interface OidcStartPayload {
  authorizeUrl: string;
  error: string | null;
}

/** Selection result for `IamLoginStart`. */
export interface LoginStartData {
  loginStart: OidcStartPayload;
}

export type LoginStartVariables = Record<string, unknown> & {
  oauthClientSqid: string;
  redirectUri: string;
  next: string;
};

/** Selection result for SDL `LoginCompletePayload` in `IamLoginComplete`. */
export interface LoginCompletePayload {
  ok: boolean;
  next: string;
  error: string | null;
}

/** Selection result for `IamLoginComplete`. */
export interface LoginCompleteData {
  loginComplete: LoginCompletePayload;
}

export type LoginCompleteVariables = Record<string, unknown> & {
  code: string;
  state: string;
  redirectUri: string;
};

export interface IAMPaginationVariables extends Record<string, unknown> {
  pagination: {
    offset: number;
    limit: number;
  };
}

/** Selection result for SDL `IAMRoleType` in `IamRoles`. */
export interface IAMRole {
  id: string;
  namespace: string;
  label: string;
  description: string;
}

/** Selection result for `IamRoles`. */
export interface IAMRolesData {
  roles: IAMRole[];
}

/** Selection result for `IamOverview`. */
export interface IAMOverviewData {
  users: {
    totalCount: number;
  };
  roles: IAMRole[];
  grants: {
    totalCount: number;
  };
  relationships: {
    totalCount: number;
  };
}

export type IAMOverviewVariables = IAMPaginationVariables;

/** Selection result for SDL `UserType` in `IamUsers`. */
export interface IAMUser extends Record<string, unknown> {
  id: string;
  username: string;
  firstName: string;
  lastName: string;
  email: string;
  isStaff: boolean;
  isActive: boolean;
}

/** Selection result for `IamUsers`. */
export interface IAMUsersData {
  users: {
    totalCount: number;
    results: IAMUser[];
  };
}

export type IAMUsersVariables = IAMPaginationVariables;

/** Selection result for SDL `IAMGrantType` in `IamGrants`. */
export interface IAMGrant {
  principalId: string;
  principalType: string;
  principalLabel: string | null;
  role: string;
}

/** Selection result for `IamGrants`. */
export interface IAMGrantsData {
  grants: {
    totalCount: number;
    results: IAMGrant[];
  };
}

export type IAMGrantsVariables = IAMPaginationVariables;

/** Selection result for SDL `IAMRelationshipType` in `IamRelationships`. */
export interface IAMRelationship {
  resourceType: string;
  resourceId: string;
  relation: string;
  subjectType: string;
  subjectId: string;
  subjectRelation: string;
  caveatName: string;
}

/** Selection result for `IamRelationships`. */
export interface IAMRelationshipsData {
  relationships: {
    totalCount: number;
    results: IAMRelationship[];
  };
}

export interface IAMRelationshipsVariables extends IAMPaginationVariables {
  resourceType?: string | null;
  subjectType?: string | null;
  relation?: string | null;
}

/** Selection result for SDL `IAMRelationType` in `IamRebacSchema`. */
export interface IAMRelationSchema {
  name: string;
  allowedSubjectTypes: string[];
}

/** Selection result for SDL `IAMPermCondition` in `IamRebacSchema`. */
export interface IAMPermissionCondition {
  name: string;
}

/** Selection result for SDL `IAMPermissionType` in `IamRebacSchema`. */
export interface IAMPermissionSchema {
  name: string;
  conditions: IAMPermissionCondition[];
}

/** Selection result for SDL `IAMResourceSchemaType` in `IamRebacSchema`. */
export interface IAMResourceSchema {
  resourceType: string;
  relations: IAMRelationSchema[];
  permissions: IAMPermissionSchema[];
}

/** Selection result for `IamRebacSchema`. */
export interface IAMRebacSchemaData {
  rebacSchema: IAMResourceSchema[];
}

/** Selection result for SDL `OAuthClientType` in `IamOAuthClients`. */
export interface IAMOAuthClient extends Record<string, unknown> {
  id: string;
  displayName: string;
  vendorLabel: string;
  vendorSlug: string;
  environment: string;
  isEnabled: boolean;
  configurationState: string;
  supportsPkce: boolean;
}

/** Selection result for `IamOAuthClients`. */
export interface IAMOAuthClientsData {
  oauthClients: {
    totalCount: number;
    results: IAMOAuthClient[];
  };
}

export type IAMOAuthClientsVariables = IAMPaginationVariables;

/** Selection result for `IamConnectionSummary`. */
export interface IAMConnectionSummaryData {
  vendors: {
    totalCount: number;
    results: IAMVendorSummary[];
  };
  externalAccounts: {
    totalCount: number;
    results: IAMExternalAccountSummary[];
  };
  credentialHealth: {
    totalCount: number;
    results: IAMCredentialSummary[];
  };
}

export type IAMConnectionSummaryVariables = IAMPaginationVariables;

/** Selection result for SDL `VendorType` in `IamConnectionSummary`. */
export interface IAMVendorSummary {
  id: string;
  slug: string;
  displayName: string;
}

/** Selection result for SDL `ExternalAccountType` in `IamConnectionSummary`. */
export interface IAMExternalAccountSummary {
  id: string;
  email: string;
  displayName: string;
  status: string;
  credentialStatus: string;
  vendor: {
    displayName: string;
  };
}

/** Selection result for SDL `CredentialType` in `IamConnectionSummary`. */
export interface IAMCredentialSummary {
  id: string;
  kind: string;
  status: string;
  lastRefreshStatus: string;
  oauthClient: {
    displayName: string;
  };
  externalAccount: {
    email: string;
  } | null;
}

/** Selection result for `IamRevokeRole`. */
export interface IAMRevokeRoleData {
  revokeRole: boolean;
}

export interface IAMRevokeRoleVariables extends Record<string, unknown> {
  principalId: string;
  role: string;
}

/** Selection result for `IamGrantRole`. */
export interface IAMGrantRoleData {
  grantRole: boolean;
}

export interface IAMGrantRoleVariables extends Record<string, unknown> {
  principalId: string;
  role: string;
}
