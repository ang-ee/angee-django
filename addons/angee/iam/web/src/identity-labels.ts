export function titleLabel(value: string): string {
  return value
    .replace(/[/_.:-]+/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

export function resourceLabel(resourceType: string): string {
  const slash = resourceType.lastIndexOf("/");
  return titleLabel(slash >= 0 ? resourceType.slice(slash + 1) : resourceType);
}

export function userLabel(user: { username: string; email: string }): string {
  return user.email ? `${user.username} <${user.email}>` : user.username;
}

export interface UserDisplayNameInput {
  display_name?: string | null;
  username?: string | null;
  email?: string | null;
}

/** IAM's canonical compact user name for cross-addon presentation. */
export function userDisplayName(user: UserDisplayNameInput, fallback: string): string {
  return user.display_name || user.username || user.email || fallback;
}
