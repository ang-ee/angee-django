// The framework preview surface: the pure mime → renderer resolver, the mime
// model, the built-in renderers, and `PreviewPane`. `PreviewPane` resolves a
// file's renderer from the built-ins plus any addon-contributed providers
// (composed at build time onto the runtime via the manifest `previews` field) —
// no module-global registry, no import side-effect.

export { PreviewPane, type PreviewPaneProps } from "./PreviewPane";
export { builtinPreviewProviders } from "./builtins";
export {
  resolvePreviewProvider,
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
  isHeicMime,
  isMarkdownMime,
  isJsonMime,
  isTextOrCodeMime,
} from "./model";
