import * as React from "react";

import {
  ControlBand,
  controlBandItemClassName,
} from "../../layouts/ControlBand";
import { ResourceToolbar, type ResourceToolbarProps } from "../../toolbars";
import { cn } from "../../lib/cn";
import { ErrorBanner } from "../../fragments/ErrorBanner";
import { Button } from "../../ui/button";
import { useUiT } from "../../i18n";
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
  onRetry?: () => void;
  summary?: string;
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
  onRetry,
  summary,
  loadingFooter = false,
  children,
  overlays,
}: ResourceListFrameProps): React.ReactElement {
  const t = useUiT();
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
        {summary ? (
          <p className="border-b border-border-subtle px-3 py-2 text-2xs text-fg-muted">
            {summary}
          </p>
        ) : null}
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
          <ErrorBanner
            description={error.message}
            actions={onRetry ? <Button size="sm" onClick={onRetry}>{t("collection.retry")}</Button> : undefined}
          />
        ) : (
          children
        )}
        {loadingFooter ? <ListLoadingFooter /> : null}
        {overlays}
      </div>
    </>
  );
}
