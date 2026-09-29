import type { Row } from "./rows";

/** Test a permission projected on a readable record by the server. */
export function holdsPermission(record: Row | null | undefined, permission: string): boolean {
  return Array.isArray(record?.permissions) && record.permissions.includes(permission);
}
