import * as React from "react";

import {
  ControlBand,
  controlBandItemClassName,
} from "../layouts/ControlBand";
import { ResourceToolbar, type ResourceToolbarProps } from "../toolbars";
import { cn } from "../lib/cn";
import {
  ListLoadingFooter,
  SelectionBar,
} from "./resource-view-list-body";

export interface ResourceListFrameSelection {
  count: number;
  onClear: () => void;
  onDelete?: () => void;
  deletePending?: boolean;
  actions?: React.ReactNode;
}

export interface ResourceListFrameProps {
  toolbar: ResourceToolbarProps;
  className?: string;
  selection?: ResourceListFrameSelection;
  error?: Error | null;
  loadingFooter?: boolean;
  children: React.ReactNode;
  overlays?: React.ReactNode;
}

/** Shared rendered frame for prepared list surfaces. */
export function ResourceListFrame({
  toolbar,
  className,
  selection,
  error = null,
  loadingFooter = false,
  children,
  overlays,
}: ResourceListFrameProps): React.ReactElement {
  return (
    <>
      <ControlBand>
        <ResourceToolbar
          {...toolbar}
          className={cn(controlBandItemClassName, toolbar.className)}
        />
      </ControlBand>
      <div
        className={cn(
          "flex min-h-full flex-col overflow-visible bg-sheet",
          className,
        )}
      >
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
