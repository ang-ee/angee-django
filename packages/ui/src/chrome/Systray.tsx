import type { ReactElement } from "react";

import { useUiT } from "../i18n";
import { Button } from "../ui/button";
import { Tooltip } from "../ui/tooltip";
import { Glyph } from "./Glyph";

export interface SystrayProps {
  onHelp?: () => void;
  onNotifications?: () => void;
}

export function Systray({
  onHelp,
  onNotifications,
}: SystrayProps): ReactElement | null {
  const t = useUiT();
  const notifications = t("chrome.notifications");
  const help = t("chrome.help");
  if (!onHelp && !onNotifications) return null;
  return (
    <div className="flex items-center gap-1">
      {onNotifications ? <Tooltip label={notifications}>
        <Button
          type="button"
          variant="icon"
          size="iconSm"
          aria-label={notifications}
          onClick={onNotifications}
          className="text-on-rail-mut hover:bg-rail-hi hover:text-on-rail-hi"
        >
          <Glyph name="bell" />
        </Button>
      </Tooltip> : null}
      {onHelp ? <Tooltip label={help}>
        <Button
          type="button"
          variant="icon"
          size="iconSm"
          aria-label={help}
          onClick={onHelp}
          className="text-on-rail-mut hover:bg-rail-hi hover:text-on-rail-hi"
        >
          <Glyph name="help" />
        </Button>
      </Tooltip> : null}
    </div>
  );
}
