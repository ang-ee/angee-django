import { type ReactElement, type ReactNode } from "react";
import { NavLink } from "@angee/ui";

import { usePlatformAddonHref } from "../lib/explorer";

/** Link a native addon name to its persisted catalogue record when readable. */
export function AddonLink({ name, children }: { name: string; children: ReactNode }): ReactElement {
  const href = usePlatformAddonHref()(name);
  return href ? <NavLink href={href} variant="inline">{children}</NavLink> : <>{children}</>;
}
