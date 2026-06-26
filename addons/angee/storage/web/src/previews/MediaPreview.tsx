import { type ReactElement } from "react";
import {
  MediaPlayer,
  MediaProvider,
  type AudioMimeType,
  type VideoMimeType,
} from "@vidstack/react";
import {
  DefaultAudioLayout,
  DefaultVideoLayout,
  defaultLayoutIcons,
} from "@vidstack/react/player/layouts/default";
import "@vidstack/react/player/styles/default/theme.css";
import "@vidstack/react/player/styles/default/layouts/video.css";
import "@vidstack/react/player/styles/default/layouts/audio.css";

import { type PreviewProviderProps } from "@angee/ui";

/** Inline player for `video/*` and `audio/*` files (vidstack). The mime it is
 * handed picks the layout — both matchers route here. */
export default function MediaPreview({
  file,
  mime,
}: PreviewProviderProps): ReactElement {
  const isAudio = mime.startsWith("audio/");
  // vidstack types a source `type` as a closed mime union but reads it at runtime
  // as a provider hint (its base `Src.type` is `string`). The matcher only routes
  // media mimes here, and that hint is what lets it select the HTML provider for
  // our extensionless token URL — a bare-string src gives it nothing to match on.
  const type = mime as AudioMimeType | VideoMimeType;
  return (
    <div className="grid h-full place-content-center bg-inset p-4">
      <MediaPlayer
        className="w-full max-w-3xl"
        title={file.name}
        src={{ src: file.url, type }}
        viewType={isAudio ? "audio" : "video"}
      >
        <MediaProvider />
        {isAudio ? (
          <DefaultAudioLayout icons={defaultLayoutIcons} />
        ) : (
          <DefaultVideoLayout icons={defaultLayoutIcons} />
        )}
      </MediaPlayer>
    </div>
  );
}
