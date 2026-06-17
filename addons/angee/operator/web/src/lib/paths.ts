// Console detail-route paths. Lists navigate to these; the operator's resources
// are keyed by name, so the segment is the percent-encoded resource name.
const OPERATOR_ROOT = "/operator";

export function serviceDetailPath(name: string): string {
  return `${OPERATOR_ROOT}/services/${encodeURIComponent(name)}`;
}

export function workspaceDetailPath(name: string): string {
  return `${OPERATOR_ROOT}/workspaces/${encodeURIComponent(name)}`;
}

export function sourceDetailPath(name: string): string {
  return `${OPERATOR_ROOT}/sources/${encodeURIComponent(name)}`;
}
