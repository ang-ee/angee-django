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
import type { ResourceCollectionPresentation } from "./resource-view-types";

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
  presentation?: ResourceCollectionPresentation;
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
  presentation = "page",
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
      <ControlBand wrap={toolbar.wrap}>
        <ResourceToolbar
          {...toolbar}
          className={cn(
            controlBandItemClassName,
            toolbar.wrap && "h-auto",
            toolbar.className,
          )}
        />
      </ControlBand>
      <div
        data-resource-presentation={presentation}
        className={cn(
          "resource-list-frame flex min-w-0 flex-col bg-sheet",
          presentation === "embedded"
            ? "overflow-visible"
            : presentation === "workspace"
              ? "min-h-0 flex-1 overflow-hidden"
              : "h-full min-h-0 overflow-hidden",
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
