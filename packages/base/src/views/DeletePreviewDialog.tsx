import * as React from "react";
import type {
  DeletePreview,
  DeletePreviewGroup,
  DeletePreviewNode,
} from "@angee/sdk";

import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { DeletePreviewTree } from "./DeletePreviewTree";

export interface DeletePreviewDialogProps {
  preview: DeletePreview;
  recordCount: number;
  blockedRecordCount?: number;
  overflowCount?: number;
  isPending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function DeletePreviewDialog({
  preview,
  recordCount,
  blockedRecordCount = 0,
  overflowCount = 0,
  isPending,
  onConfirm,
  onCancel,
}: DeletePreviewDialogProps): React.ReactElement {
  const fullyBlocked = recordCount > 0 && blockedRecordCount >= recordCount;
  const treeNodes = treeNodesFor(preview.root);
  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open && !isPending) onCancel();
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content size="lg" placement="center">
          <Dialog.Header>
            <Dialog.Title>Delete {recordCount} records?</Dialog.Title>
            <Dialog.Description>
              Review the cascade tree before deleting the selected records.
            </Dialog.Description>
          </Dialog.Header>
          <Dialog.Body className="space-y-4">
            <DeleteSummary preview={preview} overflowCount={overflowCount} />
            {preview.hasBlockers ? (
              <div className="rounded-md border border-danger/35 bg-danger-soft px-3 py-2 text-13 text-danger-text">
                {blockedRecordCount > 0
                  ? `${blockedRecordCount} selected records have deletion blockers.`
                  : "Some related records block deletion."}
              </div>
            ) : null}
            <DeletePreviewTree nodes={treeNodes} />
          </Dialog.Body>
          <Dialog.Footer>
            <Button
              type="button"
              variant="secondary"
              disabled={isPending}
              onClick={onCancel}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="danger"
              pending={isPending}
              disabled={fullyBlocked || isPending}
              onClick={onConfirm}
            >
              Delete
            </Button>
          </Dialog.Footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function DeleteSummary({
  preview,
  overflowCount,
}: {
  preview: DeletePreview;
  overflowCount: number;
}): React.ReactElement {
  return (
    <div className="grid gap-2 text-13 sm:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
      <div className="rounded-md border border-border-subtle bg-sheet px-3 py-2">
        <div className="text-12 font-medium uppercase text-fg-muted">
          Rows affected
        </div>
        <div className="mt-1 text-lg font-semibold text-fg">
          {preview.totalDeletedCount}
        </div>
        {overflowCount > 0 ? (
          <div className="mt-1 text-12 text-fg-muted">+{overflowCount} more</div>
        ) : null}
      </div>
      <div className="grid gap-2 sm:grid-cols-3">
        <GroupSummary title="Deleted" groups={preview.deleted} />
        <GroupSummary title="Updated" groups={preview.updated} />
        <GroupSummary title="Blocked" groups={preview.blocked} />
      </div>
    </div>
  );
}

function GroupSummary({
  title,
  groups,
}: {
  title: string;
  groups: readonly DeletePreviewGroup[];
}): React.ReactElement {
  const count = groups.reduce((total, group) => total + group.count, 0);
  return (
    <div className="rounded-md border border-border-subtle bg-sheet px-3 py-2">
      <div className="text-12 font-medium uppercase text-fg-muted">{title}</div>
      <div className="mt-1 font-semibold text-fg">{count}</div>
      {groups.length > 0 ? (
        <div className="mt-1 space-y-0.5 text-12 text-fg-muted">
          {groups.map((group) => (
            <div key={group.label} className="truncate">
              {group.count} {group.label}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function treeNodesFor(root: DeletePreviewNode): readonly DeletePreviewNode[] {
  return root.children.length > 0 ? root.children : [root];
}
