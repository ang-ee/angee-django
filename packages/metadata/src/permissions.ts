import type { Row } from "./rows";

/** Test a permission projected on a readable record by the server. */
export function holdsPermission(record: Row | null | undefined, permission: string): boolean {
  return Array.isArray(record?.permissions) && record.permissions.includes(permission);
}

/**
 * Whether a resource-capable action is allowed by the record's permission contract.
 * Models without a permissions field leave authorization to the server; models
 * declaring it require the permission even when the read omitted the field.
 */
export function allowsRecordPermission(
  record: Row | null | undefined,
  permission: string,
  declaresPermissions: boolean,
): boolean {
  return !declaresPermissions || holdsPermission(record, permission);
}
