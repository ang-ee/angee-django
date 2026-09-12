import { titleCase } from "@angee/ui";

export function resourceLabel(resourceType: string): string {
  const slash = resourceType.lastIndexOf("/");
  return titleCase(slash >= 0 ? resourceType.slice(slash + 1) : resourceType);
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
