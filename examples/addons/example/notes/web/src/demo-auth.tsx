import type { ReactElement } from "react";
import { Button } from "@angee/ui";

/** The seeded demo logins available on the example backend. */
const DEMO_LOGINS = [
  { username: "admin", password: "admin" },
  { username: "alice", password: "alice" },
  { username: "bob", password: "bob" },
];

/** Example-only password help that reveals seeded credentials on hover/focus. */
export function DemoForgotPasswordHint(): ReactElement {
  return (
    <div className="group relative flex justify-center">
      <Button
        type="button"
        variant="link"
        size="sm"
        aria-describedby="notes-demo-logins"
        className="!h-auto px-0 py-0 text-sm font-medium"
      >
        Forgot your password?
      </Button>
      <div
        id="notes-demo-logins"
        role="tooltip"
        className="invisible pointer-events-none absolute left-1/2 top-full z-tooltip mt-2 -translate-x-1/2 rounded-6 border border-border-subtle bg-sheet px-3 py-3 text-fg opacity-0 shadow-xl transition-opacity group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100"
      >
        <DemoLoginTooltip />
      </div>
    </div>
  );
}

function DemoLoginTooltip(): ReactElement {
  return (
    <div className="w-56 text-left">
      <p className="text-xs font-semibold uppercase tracking-wide text-fg-muted">
        Demo logins
      </p>
      <ul className="mt-2 space-y-1.5 text-13">
        {DEMO_LOGINS.map((login) => (
          <li
            key={login.username}
            className="rounded-6 bg-inset px-2.5 py-1.5 font-mono text-fg tabular-nums"
          >
            {login.username}/{login.password}
          </li>
        ))}
      </ul>
    </div>
  );
}
