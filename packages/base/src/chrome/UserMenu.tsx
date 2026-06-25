import { type ReactElement } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useAuth, useLogout } from "@angee/data";

import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
import { avatarInitials } from "../ui/avatar";
import {
  DropdownMenu,
  type DropdownMenuPositionerProps,
} from "../ui/dropdown-menu";
import { Glyph } from "./Glyph";

export interface UserMenuProps {
  className?: string;
  side?: DropdownMenuPositionerProps["side"];
  align?: DropdownMenuPositionerProps["align"];
  sideOffset?: DropdownMenuPositionerProps["sideOffset"];
}

export function UserMenu({
  className,
  side = "bottom",
  align = "end",
  sideOffset = 8,
}: UserMenuProps): ReactElement {
  const t = useBaseT();
  const { user } = useAuth();
  const { logout, fetching } = useLogout();
  const navigate = useNavigate();
  const userMenu = t("chrome.userMenu");
  const displayName = user?.name || user?.username || t("chrome.userFallback");
  const email = user?.email;

  // The menu closes itself on item select; navigate once logout succeeds.
  async function signOut(): Promise<void> {
    if (await logout()) {
      void navigate({ to: "/login" });
    }
  }

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        aria-label={userMenu}
        className={cn(
          "grid size-8 place-content-center rounded-6 border border-border-on-rail bg-avatar-default-bg text-2xs font-semibold uppercase text-on-brand outline-none transition-colors hover:bg-rail-hi focus-visible:focus-ring",
          className,
        )}
      >
        {avatarInitials(displayName)}
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Positioner side={side} align={align} sideOffset={sideOffset}>
          <DropdownMenu.Content aria-label={userMenu} className="w-60 text-13">
            <div className="border-b border-border-subtle px-3 pb-2 pt-1">
              <div className="truncate font-semibold text-fg">{displayName}</div>
              {email ? (
                <div className="truncate text-2xs text-fg-muted">{email}</div>
              ) : null}
            </div>
            <DropdownMenu.Item
              disabled={fetching}
              onClick={() => {
                void signOut();
              }}
            >
              <Glyph name="log-out" />
              <span className="flex-1 truncate">{t("chrome.signOut")}</span>
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Positioner>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
