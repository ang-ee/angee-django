import type { ReactElement } from "react";
import { heicTo } from "heic-to";

import {
  ImagePreviewSurface,
  PreviewFileContent,
  type PreviewFileReadContext,
  type PreviewProviderProps,
} from "@angee/ui";

import { useStorageT } from "../i18n";

/** Apple HEIC/HEIF photos: only Safari renders them in an `<img>`, so decode the
 * bytes to a JPEG object URL in the browser and show that. `PreviewFileContent` owns
 * the fetch, its abort, the loading and error surfaces, and revoking the URL. */
export default function HeicPreview({ file }: PreviewProviderProps): ReactElement {
  const t = useStorageT();
  const decode = async (response: Response, { signal, onCleanup }: PreviewFileReadContext): Promise<string> => {
    const jpeg = await heicTo({ blob: await response.blob(), type: "image/jpeg", quality: 0.9 }).catch((error: unknown) => {
      throw error instanceof Error ? error : new Error(t("preview.decodeFailed"));
    });
    // The decode cannot be aborted mid-flight, so check before minting an object URL.
    signal.throwIfAborted();
    const url = URL.createObjectURL(jpeg);
    onCleanup(() => URL.revokeObjectURL(url));
    return url;
  };
  return (
    <PreviewFileContent url={file.url} read={decode} loadingLabel={t("preview.decoding")}>
      {(src) => <ImagePreviewSurface src={src} alt={file.name} />}
    </PreviewFileContent>
  );
}
