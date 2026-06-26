import * as React from "react";

import { cn } from "../lib/cn";
import { Toolbar } from "../ui/toolbar";

export interface FrameToolbarProps {
  start?: React.ReactNode;
  end?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}

export function FrameToolbar({
  start,
  end,
  children,
  className,
}: FrameToolbarProps): React.ReactElement {
  return (
    <Toolbar.Root className={cn("justify-between", className)} surface="preview">
      {children ?? (
        <>
          <Toolbar.Group>{start}</Toolbar.Group>
          <Toolbar.Spacer />
          {end ? <Toolbar.Group>{end}</Toolbar.Group> : null}
        </>
      )}
    </Toolbar.Root>
  );
}

