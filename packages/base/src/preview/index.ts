// The framework preview surface: a mime → renderer registry, the pure mime
// model, and `PreviewPane`. Importing this module registers the lightweight
// built-in renderers (image, markdown, json, text); heavy renderers (pdf,
// docx, media) register from their own lazy module against the same registry.

import { registerBuiltinPreviewProviders } from "./builtins";

registerBuiltinPreviewProviders();

export { PreviewPane, type PreviewPaneProps } from "./PreviewPane";
export {
  registerPreviewProvider,
  resolvePreviewProvider,
  previewProviders,
  clearPreviewProvidersForTest,
  type PreviewFile,
  type PreviewProvider,
  type PreviewProviderProps,
  type PreviewProviderComponent,
  type PreviewMimeMatcher,
} from "./registry";
export {
  displayMime,
  normaliseMime,
  languageForFile,
  formatSize,
  isImageMime,
  isMarkdownMime,
  isJsonMime,
  isTextOrCodeMime,
} from "./model";
export { registerBuiltinPreviewProviders } from "./builtins";
