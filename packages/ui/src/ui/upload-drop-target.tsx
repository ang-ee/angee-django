import * as React from "react";

import { cn } from "../lib/cn";
import { useFileDropTarget } from "../lib/dnd";

export interface UploadDropTargetProps
  extends Omit<React.HTMLAttributes<HTMLDivElement>, "onDrop"> {
  disabled?: boolean;
  overlay?: React.ReactNode;
  overlayClassName?: string;
  onFiles: (files: readonly File[]) => void;
}

/**
 * A native browser file-drop surface. It owns the drag enter/leave depth,
 * `DataTransfer.files` filtering, copy drop effect, and overlay state; callers
 * own upload transport and copy.
 */
export const UploadDropTarget = React.forwardRef<
  HTMLDivElement,
  UploadDropTargetProps
>(function UploadDropTarget(
  {
    children,
    className,
    disabled = false,
    overlay,
    overlayClassName,
    onFiles,
    ...props
  },
  ref,
) {
  const { isOver, dropProps } = useFileDropTarget({
    disabled,
    onDrop: (files) => onFiles(files),
  });

  return (
    <div
      ref={ref}
      className={cn("relative", className)}
      data-file-drag-over={isOver ? "" : undefined}
      data-file-drop-disabled={disabled ? "" : undefined}
      {...props}
      {...dropProps}
    >
      {children}
      {isOver && overlay ? (
        <div
          className={cn(
            "pointer-events-none absolute inset-0 grid place-content-center bg-brand-soft/50 text-15 font-medium text-brand-text",
            overlayClassName,
          )}
        >
          {overlay}
        </div>
      ) : null}
    </div>
  );
});
UploadDropTarget.displayName = "UploadDropTarget";
