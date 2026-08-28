import { useEffect, useState, type ReactElement } from "react";
import { heicTo } from "heic-to";

import {
  EmptyState, LoadingPanel, type PreviewProviderProps } from "@angee/ui";

import { useStorageT } from "../i18n";

/** Apple HEIC/HEIF photos: only Safari renders them in an `<img>`, so decode the
 * bytes to a JPEG object URL in the browser and show that. The object URL is
 * revoked on unmount / when the file changes. */
export default function HeicPreview({ file }: PreviewProviderProps): ReactElement {
  const t = useStorageT();
  const [state, setState] = useState<{ url: string | null; error: Error | null }>({
    url: null,
    error: null,
  });

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | null = null;
    setState({ url: null, error: null });
    fetch(file.url, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`Preview fetch failed (${response.status})`);
        return response.blob();
      })
      .then((blob) => heicTo({ blob, type: "image/jpeg", quality: 0.9 }))
      .then((jpeg) => {
        // The decode cannot be aborted mid-flight, so re-check before committing
        // its result (and before minting an object URL that would then leak).
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(jpeg);
        setState({ url: objectUrl, error: null });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          url: null,
          error: error instanceof Error ? error : new Error("Decode failed"),
        });
      });
    return () => {
      // Aborts an in-flight fetch (so a StrictMode double-mount does not pay the
      // WASM decode twice) and frees the object URL when one was minted.
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [file.url]);

  if (state.error) {
    return (
      <EmptyState
        icon="image"
        title={t("preview.loadError")}
        description={state.error.message}
      />
    );
  }
  if (!state.url) return <LoadingPanel message={t("preview.decoding")} />;
  return (
    <div className="grid h-full place-content-center overflow-auto bg-inset p-4">
      <img
        src={state.url}
        alt={file.name}
        className="max-h-full max-w-full rounded-6 object-contain shadow-sm"
      />
    </div>
  );
}
