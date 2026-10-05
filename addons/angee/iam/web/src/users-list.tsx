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
  // Read through its field gate: empty for viewers without read__last_login and for never-signed-in people.
  "iam.last-login": { sequence: 50, content: <Column field="last_login" /> },
};

/** Shipped users-list presets, by activity. */
export const USER_LIST_PRESETS: readonly ResourceViewPreset[] = [
  { id: "iam.users.active", label: "Active", resource: USER_MODEL, filter: { is_active: { exact: true } } },
  { id: "iam.users.deactivated", label: "Deactivated", resource: USER_MODEL, filter: { is_active: { exact: false } } },
];

export const USER_LIST_PRESET_IDS = USER_LIST_PRESETS.map((preset) => preset.id);
