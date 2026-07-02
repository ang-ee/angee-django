import { builtinPreviewProviders, displayMime, resolvePreviewProvider } from "@angee/ui";
import { describe, expect, test } from "vitest";

import storage from "./index";
import { storagePreviews } from "./previews";

// Resolution and registration only — these run in happy-dom and never decode or
// render. The renderers' heavy deps (heic-to WASM, react-pdf canvas, vidstack)
// need a real browser and are exercised manually via `angee dev`. `lazy` keeps
// those imports deferred, so importing the manifest here stays cheap.
//
// `PreviewPane` lists runtime providers before the built-ins; mirror that order.
const providers = [...storagePreviews, ...builtinPreviewProviders];

describe("storage preview providers", () => {
  test("the manifest contributes the rich renderers", () => {
    expect(storage.previews?.map((provider) => provider.id)).toEqual([
      "storage.pdf",
      "storage.video",
      "storage.audio",
      "storage.heic",
    ]);
  });

  test("pdf, video, and audio resolve to the storage renderers", () => {
    expect(resolvePreviewProvider(providers, "application/pdf")?.id).toBe("storage.pdf");
    expect(resolvePreviewProvider(providers, "video/mp4")?.id).toBe("storage.video");
    expect(resolvePreviewProvider(providers, "audio/mpeg")?.id).toBe("storage.audio");
  });

  test("HEIC outranks the built-in image renderer for every HEIC mime", () => {
    for (const mime of [
      "image/heic",
      "image/heif",
      "image/heic-sequence",
      "image/heif-sequence",
    ]) {
      expect(resolvePreviewProvider(providers, mime)?.id).toBe("storage.heic");
    }
  });

  test("a plain image still resolves to the built-in image renderer", () => {
    expect(resolvePreviewProvider(providers, "image/png")?.id).toBe("base.image");
  });

  test("a .HEIC file whose server mime is unknown still routes to the decoder", () => {
    // libmagic does not always recognise HEIC and stores octet-stream;
    // `PreviewPane` derives the mime via `displayMime`, which recovers it from
    // the filename, so the decoding renderer still wins.
    const file = {
      url: "#",
      name: "IMG_9803.HEIC",
      mime: "application/octet-stream",
      size: null,
    };
    expect(resolvePreviewProvider(providers, displayMime(file))?.id).toBe("storage.heic");
  });
});
