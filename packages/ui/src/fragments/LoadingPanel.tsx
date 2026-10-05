import * as React from "react";

import { useUiT } from "../i18n";
import { ControlBand } from "../layouts/ControlBand";
import { cn } from "../lib/cn";
import { Skeleton, SkeletonStatus } from "../ui/skeleton";
import { Spinner } from "../ui/spinner";
import { Table, TableBody, TableCell, TableRow } from "../ui/table";
import { textRoleVariants } from "../ui/text";

export interface LoadingPanelProps {
  message?: string;
  /** Size of the centred status; the page shape fills its region. */
  density?: "page" | "inline";
  /**
   * `status` centres a spinner for a region whose final layout is unknowable.
   * `page` is the neutral shape of a console page whose route is loading: an
   * empty control band row over placeholder rows.
   */
  shape?: "status" | "page";
}

const PAGE_ROW_WIDTHS = ["w-2/3", "w-1/2", "w-3/4", "w-2/5", "w-3/5", "w-1/2"] as const;

/** Unboxed pending status for boundaries that cannot know the final layout; the router's page fallback takes the page shape. */
export function LoadingPanel({
  message,
  density = "page",
  shape = "status",
}: LoadingPanelProps): React.ReactElement {
  const t = useUiT();
  const label = message ?? t("loading.default");
  const inline = density === "inline";

  if (shape === "page") {
    return (
      <SkeletonStatus label={label} className="flex h-full min-h-0 flex-col">
        <ControlBand>{null}</ControlBand>
        <Table aria-hidden="true">
          <TableBody>
            {PAGE_ROW_WIDTHS.map((width, index) => (
              <TableRow key={index}>
                <TableCell><Skeleton shape="text" size="sm" className={width} /></TableCell>
                <TableCell className="w-1/4"><Skeleton shape="text" size="sm" className="w-2/3" /></TableCell>
                <TableCell className="w-24"><Skeleton shape="text" size="sm" className="ml-auto w-12" /></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </SkeletonStatus>
    );
  }

  return (
    <SkeletonStatus
      label={label}
      className={cn(
        "grid place-content-center",
        inline ? "min-h-16 p-3" : "h-full min-h-32 p-8",
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          textRoleVariants({ role: "meta" }),
          "inline-flex items-center gap-2",
        )}
      >
        <Spinner size="sm" tone="muted" />
        {label}
      </span>
    </SkeletonStatus>
  );
}
