import { useState, type ReactElement } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useAuth, useLogout } from "@angee/sdk";

import { cn } from "../lib/cn";
import {
  PopoverContent,
  PopoverPortal,
  PopoverPositioner,
  type PopoverPositionerProps,
  PopoverRoot,
  PopoverTrigger,
} from "../ui/popover";
import { Glyph } from "./Glyph";

export interface UserMenuProps {
  className?: string;
  side?: PopoverPositionerProps["side"];
  align?: PopoverPositionerProps["align"];
  sideOffset?: PopoverPositionerProps["sideOffset"];
}

export function UserMenu({
  className,
  side = "bottom",
  align = "end",
  sideOffset = 8,
}: UserMenuProps): ReactElement {
  const { user } = useAuth();
  const { logout, fetching } = useLogout();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const displayName = user?.name || user?.username || "User";
  const email = user?.email;

  async function signOut(): Promise<void> {
    if (await logout()) {
      setOpen(false);
      void navigate({ to: "/login" });
    }
  }

  return (
    <PopoverRoot open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        aria-label="User menu"
        className={cn(
          "grid size-8 place-content-center rounded-6 border border-border-on-rail bg-avatar-default-bg text-2xs font-semibold uppercase text-on-brand outline-none transition-colors hover:bg-rail-hi focus-visible:focus-ring",
          className,
        )}
      >
        {initials(displayName)}
      </PopoverTrigger>
      <PopoverPortal>
        <PopoverPositioner side={side} align={align} sideOffset={sideOffset}>
          <PopoverContent
            aria-label="User menu"
            className="w-60 p-1 text-13"
            role="menu"
            surface="sheet"
          >
            <div className="border-b border-border-subtle p-3">
              <div className="truncate font-semibold text-fg">{displayName}</div>
              {email ? (
                <div className="truncate text-2xs text-fg-muted">{email}</div>
              ) : null}
            </div>
            <button
              type="button"
              role="menuitem"
              disabled={fetching}
              onClick={() => {
                void signOut();
              }}
              className="flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-fg-2 outline-none transition-colors hover:bg-inset hover:text-fg focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-60"
            >
              <Glyph name="log-out" />
              <span className="flex-1 truncate">Sign out</span>
            </button>
          </PopoverContent>
        </PopoverPositioner>
      </PopoverPortal>
    </PopoverRoot>
  );
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const letters =
    parts.length >= 2
      ? `${parts[0]?.[0] ?? ""}${parts[1]?.[0] ?? ""}`
      : name.slice(0, 2);
  return letters.toUpperCase() || "U";
}
