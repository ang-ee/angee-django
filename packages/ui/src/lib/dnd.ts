import {
  useCallback,
  useRef,
  useState,
  type DragEventHandler,
  type DragEvent,
} from "react";

/**
 * The drag-and-drop seam. The framework owns the wire format — one internal
 * MIME carrying a JSON payload — and exposes draggable/droppable behavior as
 * hooks; a consumer supplies only its payload shape and a `canDrop` guard.
 *
 * Native HTML5 DnD is used here, not `@dnd-kit` (which owns within-context
 * board/rail interactions): native drag crosses unrelated subtrees — a list row
 * dropped onto a sidebar tree node — without a shared `DndContext`, which is
 * exactly the cross-pane move the Explorer needs.
 */

/** The single payload MIME; the payload's `type` field discriminates kinds. */
export const DND_MIME = "application/vnd.angee.dnd+json";

// HTML5 hides the payload body during `dragover` (only `drop` can read it) but
// the list of available *types* stays visible. We mirror the payload `type`
// into a marker MIME so a drop target can light up (or refuse) before the drop.
// DataTransfer lowercases type keys, so the discriminator is case-insensitive.
const DND_TYPE_MARKER_PREFIX = "application/vnd.angee.dnd.type.";

function typeMarker(type: string): string {
  return `${DND_TYPE_MARKER_PREFIX}${type.toLowerCase()}`;
}

/**
 * A dragged item. `type` is a consumer-defined kind (e.g. `"storage.file"`);
 * `data` is the small serialisable payload the drop handler acts on.
 */
export interface DndPayload<TData = unknown> {
  type: string;
  data: TData;
}

/** Write a payload onto a drag event's DataTransfer (body + type marker). */
export function writeDndPayload(
  dataTransfer: DataTransfer,
  payload: DndPayload,
): void {
  dataTransfer.setData(DND_MIME, JSON.stringify(payload));
  dataTransfer.setData(typeMarker(payload.type), "");
  dataTransfer.effectAllowed = "move";
}

/** Read a payload from a DataTransfer, or `null` if absent/invalid. */
export function readDndPayload<TData = unknown>(
  dataTransfer: DataTransfer,
): DndPayload<TData> | null {
  const raw = dataTransfer.getData(DND_MIME);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as DndPayload<TData>;
    return parsed && typeof parsed.type === "string" ? parsed : null;
  } catch {
    return null;
  }
}

function acceptList(
  accept: string | readonly string[] | undefined,
): readonly string[] | null {
  if (accept == null) return null;
  return typeof accept === "string" ? [accept] : accept;
}

/**
 * Whether an in-flight drag carries an accepted payload, judged from the
 * visible type markers (usable during `dragover`). With no `accept`, any angee
 * payload qualifies.
 */
export function dragHasAcceptedType(
  dataTransfer: Pick<DataTransfer, "types">,
  accept?: string | readonly string[],
): boolean {
  const types = Array.from(dataTransfer.types);
  const wanted = acceptList(accept);
  if (!wanted) return types.includes(DND_MIME);
  return wanted.some((type) => types.includes(typeMarker(type)));
}

/** Whether the native browser drag currently carries one or more files. */
export function dragHasFiles(
  dataTransfer: Pick<DataTransfer, "types" | "files">,
): boolean {
  const types = Array.from(dataTransfer.types, (type) => type.toLowerCase());
  return types.includes("files") || (dataTransfer.files?.length ?? 0) > 0;
}

/**
 * Make an element draggable with a typed payload — spread the result onto any
 * element. Pass `null` (or a getter returning `null`) to disable for a render.
 */
export function useDraggable<TData = unknown>(
  payload: DndPayload<TData> | null | (() => DndPayload<TData> | null),
): { draggable: boolean; onDragStart: DragEventHandler } {
  const onDragStart = useCallback<DragEventHandler>(
    (event) => {
      const resolved = typeof payload === "function" ? payload() : payload;
      if (!resolved) {
        event.preventDefault();
        return;
      }
      writeDndPayload(event.dataTransfer, resolved);
    },
    [payload],
  );
  return {
    draggable: typeof payload === "function" ? true : payload != null,
    onDragStart,
  };
}

export type DragSourceProps =
  | { draggable: true; onDragStart: DragEventHandler }
  | undefined;

/**
 * Native drag-source props for an element carrying a typed payload — spread the
 * result onto the element. Returns `undefined` (no `draggable`) when the payload
 * is null, so a non-draggable row/card stays inert. The non-hook companion to
 * {@link useDraggable}, for per-item maps where a hook can't run (list rows,
 * gallery cards).
 */
export function dragSourceProps(payload: DndPayload | null): DragSourceProps {
  if (!payload) return undefined;
  return {
    draggable: true,
    onDragStart: (event) => writeDndPayload(event.dataTransfer, payload),
  };
}

export interface UseDropTargetOptions<TData = unknown> {
  /** Payload `type`s this target accepts. Omit to accept any angee payload. */
  accept?: string | readonly string[];
  /** Reject a decoded payload at drop time (e.g. a folder onto itself). */
  canDrop?: (payload: DndPayload<TData>) => boolean;
  /** Called with the decoded payload when an accepted item is dropped. */
  onDrop: (payload: DndPayload<TData>) => void;
}

export interface UseFileDropTargetOptions {
  disabled?: boolean;
  /** Called with browser `File`s dropped onto the target. */
  onDrop: (files: readonly File[], event: DragEvent) => void;
}

/**
 * Make an element a drop target. Returns `isOver` (true while an accepted
 * payload hovers — for highlight) and `dropProps` to spread. `accept` gates the
 * hover highlight via the visible type markers; `canDrop` runs once the body is
 * readable at drop time.
 */
export function useDropTarget<TData = unknown>({
  accept,
  canDrop,
  onDrop,
}: UseDropTargetOptions<TData>): {
  isOver: boolean;
  dropProps: {
    onDragOver: DragEventHandler;
    onDragLeave: DragEventHandler;
    onDrop: DragEventHandler;
  };
} {
  const [isOver, setIsOver] = useState(false);
  // Track enter/leave depth so moving over child elements doesn't flicker.
  const depth = useRef(0);

  const onDragOver = useCallback<DragEventHandler>(
    (event) => {
      if (!dragHasAcceptedType(event.dataTransfer, accept)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      depth.current += 1;
      setIsOver(true);
    },
    [accept],
  );
  const onDragLeave = useCallback<DragEventHandler>(() => {
    depth.current = Math.max(0, depth.current - 1);
    if (depth.current === 0) setIsOver(false);
  }, []);
  const handleDrop = useCallback<DragEventHandler>(
    (event) => {
      depth.current = 0;
      setIsOver(false);
      const payload = readDndPayload<TData>(event.dataTransfer);
      if (!payload) return;
      const wanted = acceptList(accept);
      if (wanted && !wanted.includes(payload.type)) return;
      if (canDrop && !canDrop(payload)) return;
      event.preventDefault();
      onDrop(payload);
    },
    [accept, canDrop, onDrop],
  );

  return { isOver, dropProps: { onDragOver, onDragLeave, onDrop: handleDrop } };
}

/**
 * Make an element a native file drop target. This is separate from Angee's
 * internal typed DnD payload seam: browser file drops use `DataTransfer.files`,
 * not the JSON payload MIME used for row/card moves.
 */
export function useFileDropTarget({
  disabled = false,
  onDrop,
}: UseFileDropTargetOptions): {
  isOver: boolean;
  dropProps: {
    onDragEnter: DragEventHandler;
    onDragOver: DragEventHandler;
    onDragLeave: DragEventHandler;
    onDrop: DragEventHandler;
  };
} {
  const [isOver, setIsOver] = useState(false);
  const depth = useRef(0);

  const onDragEnter = useCallback<DragEventHandler>(
    (event) => {
      if (disabled || !dragHasFiles(event.dataTransfer)) return;
      event.preventDefault();
      depth.current += 1;
      setIsOver(true);
    },
    [disabled],
  );

  const onDragOver = useCallback<DragEventHandler>(
    (event) => {
      if (!dragHasFiles(event.dataTransfer)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = disabled ? "none" : "copy";
      if (disabled) return;
      setIsOver(true);
    },
    [disabled],
  );

  const onDragLeave = useCallback<DragEventHandler>(() => {
    depth.current = Math.max(0, depth.current - 1);
    if (depth.current === 0) setIsOver(false);
  }, []);

  const handleDrop = useCallback<DragEventHandler>(
    (event) => {
      depth.current = 0;
      setIsOver(false);
      if (!dragHasFiles(event.dataTransfer)) return;
      event.preventDefault();
      if (disabled) return;
      const files = Array.from(event.dataTransfer.files);
      if (files.length === 0) return;
      onDrop(files, event);
    },
    [disabled, onDrop],
  );

  return {
    isOver,
    dropProps: { onDragEnter, onDragOver, onDragLeave, onDrop: handleDrop },
  };
}
