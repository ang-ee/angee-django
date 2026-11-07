import * as React from "react";

import { useUiT } from "../i18n";
import { ControlBand } from "../layouts/ControlBand";
import { cn } from "../lib/cn";
import { Skeleton, SkeletonStatus, SkeletonText } from "../ui/skeleton";
import { Table, TableBody, TableCell, TableRow } from "../ui/table";

export interface LoadingPanelProps {
  /** The accessible pending label; it is announced, never rendered as visible text. */
  loadingLabel?: string;
  /** Line count and height of the status skeleton; the page shape fills its region. */
  density?: "page" | "inline";
  /**
   * `status` draws neutral skeleton lines for a region whose final layout is
   * unknowable. `page` is the neutral shape of a console page whose route is
   * loading: an empty control band row over placeholder rows.
   */
  shape?: "status" | "page";
}

const PAGE_ROW_WIDTHS = ["w-2/3", "w-1/2", "w-3/4", "w-2/5", "w-3/5", "w-1/2"] as const;

/** Unboxed pending skeleton for boundaries that cannot know the final layout; the router's page fallback takes the page shape.
 * Every shape is a skeleton: visible loading copy is never a loading state.
 * @kind fragment
 */
export function LoadingPanel({
  loadingLabel,
  density = "page",
  shape = "status",
}: LoadingPanelProps): React.ReactElement {
  const t = useUiT();
  const label = loadingLabel ?? t("loading.default");
  const inline = density === "inline";

  if (shape === "page") {
    return (
      <SkeletonStatus loadingLabel={label} className="flex h-full min-h-0 flex-col">
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
    <SkeletonStatus loadingLabel={label} className={cn("w-full", inline ? "p-3" : "h-full min-h-32 p-8")}>
      {inline ? <SkeletonText lines={2} /> : <SkeletonText lines={3} size="md" className="max-w-2xl" />}
    </SkeletonStatus>
  );
}
