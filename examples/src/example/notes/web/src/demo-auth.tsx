import type { ReactElement } from "react";
import { Button, Code, Tooltip } from "@angee/ui";

import { useNotesT } from "./i18n";

/** The seeded demo logins available on the example backend. */
const DEMO_LOGINS = [
  { username: "admin", password: "admin" },
  { username: "alice", password: "alice" },
  { username: "bob", password: "bob" },
];

/** Example-only password help: the seeded credentials are the link's tooltip. */
export function DemoForgotPasswordHint(): ReactElement {
  const t = useNotesT();
  return (
    <div className="flex justify-center">
      <Tooltip side="bottom" label={<DemoLogins label={t("demo.logins")} />}>
        <Button type="button" variant="link" size="sm">
          {t("demo.forgotPassword")}
        </Button>
      </Tooltip>
    </div>
  );
}

function DemoLogins({ label }: { label: string }): ReactElement {
  return (
    <div className="grid gap-1">
      <span>{label}</span>
      {DEMO_LOGINS.map((login) => (
        <Code key={login.username}>{login.username}/{login.password}</Code>
      ))}
    </div>
  );
}
