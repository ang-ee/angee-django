import type { ResourceViewPreset } from "@angee/app";
import { Column } from "@angee/ui";

/** The IAM user model every account surface addresses. */
export const USER_MODEL = "iam.User";

/**
 * IAM's own `iam.users#columns` children. Other addons place seat or role
 * columns among them by `sequence`, and narrow or hide them like any child.
 */
export const USER_LIST_COLUMNS = {
  "iam.username": { sequence: 10, content: <Column field="username" /> },
  "iam.email": { sequence: 20, content: <Column field="email" /> },
  "iam.is-staff": { sequence: 30, content: <Column field="is_staff" /> },
  "iam.is-active": { sequence: 40, content: <Column field="is_active" /> },
  // Present where the viewer reads every row's last sign-in (read__last_login at type level); the field
  // gate still decides each value, empty for never-signed-in people.
  "iam.last-login": { sequence: 50, requires: "iam.User#read__last_login", content: <Column field="last_login" /> },
};

/**
 * Shipped users-list presets, by activity. Activity is a restricted field, so they filter on the
 * resource's `active` expression, which matches only rows whose viewer may read it.
 */
export const USER_LIST_PRESETS: readonly ResourceViewPreset[] = [
  { id: "iam.users.active", label: "Active", resource: USER_MODEL, filter: { active: { exact: true } } },
  { id: "iam.users.deactivated", label: "Deactivated", resource: USER_MODEL, filter: { active: { exact: false } } },
];

/** The managed people list's rows: the viewer's people directory, empty for anyone who manages no one. */
export const USER_DIRECTORY_FILTER = { directory: { exact: true } } as const;

export const USER_LIST_PRESET_IDS = USER_LIST_PRESETS.map((preset) => preset.id);
