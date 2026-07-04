export function browserLocalStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    const storage = window.localStorage;
    return isStorage(storage) ? storage : null;
  } catch {
    return null;
  }
}

function isStorage(value: unknown): value is Storage {
  return (
    value != null
    && typeof (value as Storage).getItem === "function"
    && typeof (value as Storage).setItem === "function"
    && typeof (value as Storage).removeItem === "function"
  );
}
