import type { ReactElement } from "react";

/** The seeded demo logins, listed under the login form. */
const DEMO_LOGINS = [
  { username: "alice", password: "alice" },
  { username: "bob", password: "bob" },
];

/** A hint listing the demo credentials available on the example backend. */
export function DemoCredentials(): ReactElement {
  return (
    <div className="text-13 text-fg-muted">
      <p className="font-medium text-fg">Demo logins</p>
      <ul className="mt-1 flex flex-col gap-0.5">
        {DEMO_LOGINS.map((login) => (
          <li key={login.username} className="tabular-nums">
            <span className="font-medium text-fg">{login.username}</span>
            {" / "}
            {login.password}
          </li>
        ))}
      </ul>
    </div>
  );
}
