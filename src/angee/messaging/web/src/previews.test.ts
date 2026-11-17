import { builtinPreviewProviders, resolvePreviewProvider } from "@angee/ui";
import { describe, expect, test } from "vitest";

import messaging from "./index";
import { messagingPreviews } from "./previews";

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

  test("an HTML file keeps the built-in source view", () => {
    expect(resolvePreviewProvider(providers, "text/html")?.id).toBe("base.text");
  });
});
