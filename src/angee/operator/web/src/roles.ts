/**
 * These gate the operator console once route/nav role-gating (G1/G2) lands; until then the server REBAC enforces.
 */
export const OPERATOR_ROLE_ADMIN = "operator/role:operator_admin";
export const ANGEE_ROLE_ADMIN = "angee/role:admin";

export const OPERATOR_ADMIN_ROLES = [
  OPERATOR_ROLE_ADMIN,
  ANGEE_ROLE_ADMIN,
] as const;
