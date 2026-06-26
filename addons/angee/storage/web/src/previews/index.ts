import { lazy } from "react";
import { isHeicMime, type PreviewProvider } from "@angee/ui";

// Each renderer is lazy, so its heavy deps (react-pdf, vidstack, heic-to) only
// load when that file kind is actually opened; `PreviewPane` supplies the
// Suspense fallback.
const PdfPreview = lazy(() => import("./PdfPreview"));
const MediaPreview = lazy(() => import("./MediaPreview"));
const HeicPreview = lazy(() => import("./HeicPreview"));

/**
 * The rich file renderers the storage addon contributes to `PreviewPane`, on top
 * of the framework built-ins (image/markdown/json/text/fallback). `storage.heic`
 * sits above the built-in `image/*` renderer (priority 0): HEIC mimes are a
 * subset of `image/*` that only Safari can decode in an `<img>`, so the decoding
 * renderer must win for them.
 */
export const storagePreviews: readonly PreviewProvider[] = [
  { id: "storage.pdf", mime: "application/pdf", component: PdfPreview },
  { id: "storage.video", mime: "video/*", component: MediaPreview },
  { id: "storage.audio", mime: "audio/*", component: MediaPreview },
  { id: "storage.heic", mime: isHeicMime, component: HeicPreview, priority: 10 },
];
