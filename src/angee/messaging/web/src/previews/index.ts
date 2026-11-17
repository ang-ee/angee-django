import { lazy } from "react";
import type { PreviewProvider } from "@angee/ui";

// Lazy, so the MIME parser loads only when an email file is opened; `PreviewPane`
// supplies the Suspense fallback.
const EmailPreview = lazy(() => import("./EmailPreview"));

/**
 * The renderers messaging contributes to `PreviewPane`. An email file
 * (`message/rfc822`, as IMAP keeps raw mail) previews as its message. No `text/html`
 * provider: an HTML file keeps the built-in source view.
 */
export const messagingPreviews: readonly PreviewProvider[] = [
  { id: "messaging.email", mime: "message/rfc822", component: EmailPreview },
];
