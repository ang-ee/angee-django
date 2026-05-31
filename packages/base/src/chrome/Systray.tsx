import type { ReactElement } from "react";

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
}: SystrayProps): ReactElement {
  return (
    <div className="flex items-center gap-1">
      <Tooltip label="Notifications">
        <Button
          type="button"
          variant="icon"
          size="iconSm"
          aria-label="Notifications"
          onClick={onNotifications}
          className="text-on-rail-mut hover:bg-rail-hi hover:text-on-rail-hi"
        >
          <Glyph name="bell" />
        </Button>
      </Tooltip>
      <Tooltip label="Help">
        <Button
          type="button"
          variant="icon"
          size="iconSm"
          aria-label="Help"
          onClick={onHelp}
          className="text-on-rail-mut hover:bg-rail-hi hover:text-on-rail-hi"
        >
          <Glyph name="help" />
        </Button>
      </Tooltip>
    </div>
  );
}
