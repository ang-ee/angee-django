export interface GroupPagerState {
  total: number;
  fetching: boolean;
  error: Error | null;
}

export function groupPagerStatesEqual(
  left: GroupPagerState | null,
  right: GroupPagerState,
): boolean {
  return (
    left !== null &&
    left.total === right.total &&
    left.fetching === right.fetching &&
    left.error === right.error
  );
}
