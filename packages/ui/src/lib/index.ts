// Styling foundation: the one class-merge config, the `cn` helper, and the
// `tv` recipe factory every component recipe is built on.

export { cn } from "./cn";
export { titleCase } from "./titleCase";
export { statusLabel } from "./labels";
export { tv, type VariantProps } from "./variants";
export { ANGEE_TW_MERGE_CONFIG } from "./tailwind-merge-config";
export {
  TONES,
  FILLS,
  toneFill,
  toneClass,
  INTENT_GLYPHS,
  FEEDBACK_INTENTS,
  stateToneFromValue,
  type Tone,
  type Fill,
  type FeedbackIntent,
  type ToneValueBuckets,
} from "./tones";
export { useRender } from "./slot";
export type {
  UseRenderComponentProps,
  UseRenderRenderProp,
} from "./slot";
export { SlotOutlet, slotEntriesHaveContent } from "./slot-outlet";
export {
  DND_MIME,
  writeDndPayload,
  readDndPayload,
  dragHasAcceptedType,
  dragHasFiles,
  useDraggable,
  dragSourceProps,
  useDropTarget,
  useFileDropTarget,
  type DndPayload,
  type DragSourceProps,
  type UseDropTargetOptions,
  type UseFileDropTargetOptions,
} from "./dnd";
