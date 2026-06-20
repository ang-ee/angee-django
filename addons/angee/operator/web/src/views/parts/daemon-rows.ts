/** A daemon record projected into the row shape expected by `RowsListView`. */
export type DaemonRow<T extends object> = T & { id: string };

export function daemonRows<T extends object>(
  items: readonly T[],
  id: (item: T) => string,
): readonly DaemonRow<T>[] {
  return items.map((item) => ({ ...item, id: id(item) }));
}

export function daemonRowsByName<T extends { name: string }>(
  items: readonly T[],
): readonly DaemonRow<T>[] {
  return daemonRows(items, (item) => item.name);
}
