import { test as setup, loginViaApi, roleStatePath, type Credentials } from "@angee/e2e";

/**
 * The demo logins the workspace seeds via `resources load demo`. Each is
 * authenticated once here and its session persisted, so specs start logged in
 * without re-authenticating per test.
 */
const ROLES: Credentials[] = [
  { username: "admin", password: "admin" },
  { username: "alice", password: "alice" },
  { username: "bob", password: "bob" },
];

for (const role of ROLES) {
  setup(`authenticate ${role.username}`, async ({ request }) => {
    await loginViaApi(request, role);
    await request.storageState({ path: roleStatePath(role.username) });
  });
}
