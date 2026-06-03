import {
  Card,
  CardContent,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@angee/base";
import { useT } from "@angee/sdk";
import type { ReactNode } from "react";

import { useOperatorSnapshot } from "../../data/transport";
import type { GitOpsSummary } from "../../data/types";
import { SectionError, SectionLoading } from "../parts/SectionStatus";
import { StateTag } from "../parts/StateTag";

interface SummaryTile {
  id: keyof GitOpsSummary;
  label: string;
}

// A read-only subset of the numeric summary fields, rendered as stat tiles.
const SUMMARY_TILES: readonly SummaryTile[] = [
  { id: "clean", label: "Clean" },
  { id: "dirty", label: "Dirty" },
  { id: "ahead", label: "Ahead" },
  { id: "behind", label: "Behind" },
  { id: "diverged", label: "Diverged" },
  { id: "unpushed", label: "Unpushed" },
];

/** GitOps pane: a read-only summary + per-link drift table from the daemon topology. */
export function GitOpsSection(): ReactNode {
  const t = useT("operator");
  const { snapshot, result } = useOperatorSnapshot({ gitOps: true });

  if (result.error && !snapshot) {
    return <SectionError message={result.error.message} />;
  }
  if (result.fetching && !snapshot) {
    return <SectionLoading label="Loading GitOps topology" />;
  }

  const gitOps = snapshot?.gitOps ?? null;

  if (!gitOps) {
    return (
      <div className="flex flex-col gap-4">
        <h2 className="text-lg font-semibold text-fg">{t("section.gitops.title")}</h2>
        <SectionError message="No GitOps topology." />
      </div>
    );
  }

  const { summary, links } = gitOps;

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold text-fg">{t("section.gitops.title")}</h2>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {SUMMARY_TILES.map((tile) => (
          <Card key={tile.id}>
            <CardContent className="flex flex-col gap-1 py-4">
              <span className="text-2xl font-semibold tabular-nums text-fg">
                {summary[tile.id]}
              </span>
              <span className="text-13 text-fg-muted">{tile.label}</span>
            </CardContent>
          </Card>
        ))}
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Source</TableHead>
            <TableHead>Workspace</TableHead>
            <TableHead>Slot</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Branch</TableHead>
            <TableHead className="text-right">Ahead/Behind</TableHead>
            <TableHead>Pushed</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {links.length === 0 ? (
            <TableRow>
              <TableCell className="text-center text-13 text-fg-muted" colSpan={7}>
                No GitOps links.
              </TableCell>
            </TableRow>
          ) : (
            links.map((link) => (
              <TableRow key={link.id}>
                <TableCell className="font-medium text-fg">{link.source}</TableCell>
                <TableCell className="text-13 text-fg-muted">{link.workspace}</TableCell>
                <TableCell className="text-13 text-fg-muted">{link.slot}</TableCell>
                <TableCell>
                  <StateTag state={link.state} />
                </TableCell>
                <TableCell className="text-13 text-fg-muted">{link.branch ?? "—"}</TableCell>
                <TableCell className="text-right text-13 tabular-nums text-fg-muted">
                  ↑{link.ahead ?? 0} ↓{link.behind ?? 0}
                </TableCell>
                <TableCell className="text-13 text-fg-muted">
                  {link.pushed ? "yes" : "no"}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}
