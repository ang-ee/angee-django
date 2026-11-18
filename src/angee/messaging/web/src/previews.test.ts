import { builtinPreviewProviders, resolvePreviewProvider } from "@angee/ui";
import { describe, expect, test } from "vitest";

import messaging from "./index";
import { messagingPreviews } from "./previews";
import { EMAIL_PREVIEW_MAX_BYTES } from "./previews/email-size";

// Resolution and registration only; the renderer is lazy, so importing the
// manifest stays cheap. `PreviewPane` lists runtime providers before the built-ins.
const providers = [...messagingPreviews, ...builtinPreviewProviders];

describe("messaging preview providers", () => {
  test("the manifest contributes the email previewer", () => {
    expect(messaging.previews?.map((provider) => provider.id)).toEqual(["messaging.email"]);
  });

  test("an email file resolves to the email previewer", () => {
    expect(resolvePreviewProvider(providers, "message/rfc822")?.id).toBe("messaging.email");
    expect(resolvePreviewProvider(providers, "message/rfc822; charset=utf-8")?.id).toBe("messaging.email");
  });

  test("an email file larger than the cap falls through to the generic file fallback", () => {
    expect(resolvePreviewProvider(providers, "message/rfc822", EMAIL_PREVIEW_MAX_BYTES)?.id).toBe("messaging.email");
    expect(resolvePreviewProvider(providers, "message/rfc822", EMAIL_PREVIEW_MAX_BYTES + 1)?.id).toBe("base.fallback");
  });

  test("an HTML file keeps the built-in source view", () => {
    expect(resolvePreviewProvider(providers, "text/html")?.id).toBe("base.text");
  });
});
