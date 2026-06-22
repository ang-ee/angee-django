import * as React from "react";

import {
  ControlBand,
  controlBandItemClassName,
} from "../shell/ControlBand";
import { DataToolbar, type DataToolbarProps } from "../toolbars";
import { cn } from "../lib/cn";
import {
  ListLoadingFooter,
  SelectionBar,
} from "./ListInternals";

export interface DataViewListShellSelection {
  count: number;
  onClear: () => void;
  onDelete?: () => void;
  deletePending?: boolean;
  actions?: React.ReactNode;
}

export interface DataViewListShellProps {
  toolbar: DataToolbarProps;
  className?: string;
  selection?: DataViewListShellSelection;
  error?: Error | null;
  loadingFooter?: boolean;
  children: React.ReactNode;
  overlays?: React.ReactNode;
}

/** Shared rendered frame for prepared list surfaces. */
export function DataViewListShell({
  toolbar,
  className,
  selection,
  error = null,
  loadingFooter = false,
  children,
  overlays,
}: DataViewListShellProps): React.ReactElement {
  return (
    <>
      <ControlBand>
        <DataToolbar
          {...toolbar}
          className={cn(controlBandItemClassName, toolbar.className)}
        />
      </ControlBand>
      <div className={cn("min-h-0 overflow-hidden bg-sheet", className)}>
        {selection && selection.count > 0 ? (
          <SelectionBar
            count={selection.count}
            onClear={selection.onClear}
            onDelete={selection.onDelete}
            deletePending={selection.deletePending}
            actions={selection.actions}
          />
        ) : null}
        {error ? (
          <div className="px-3 py-6 text-13 text-danger-text">
            {error.message}
          </div>
        ) : (
          children
        )}
        {loadingFooter ? <ListLoadingFooter /> : null}
        {overlays}
      </div>
    </>
  );
}
