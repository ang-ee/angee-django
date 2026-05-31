// @angee/base — the single rendered binding for @angee/sdk. Styled primitives,
// data-bound views, app shell, and the auth surface, composed onto base-ui +
// tailwind-variants. The SDK stays headless; rendering lives here.

// Styling foundation.
export { cn, tv, ANGEE_TW_MERGE_CONFIG, type VariantProps } from "./lib";

// i18n — the base namespace translator.
export { useBaseT, enBaseMessages } from "./i18n";

// UI primitives — embeddable, SDK-agnostic atoms on base-ui.
export * from "./ui/button";
export * from "./ui/spinner";
export * from "./ui/field";
export * from "./ui/label";
export * from "./ui/input";
export * from "./ui/textarea";
export * from "./ui/select";
export * from "./ui/checkbox";
export * from "./ui/switch";
export * from "./ui/dialog";
export * from "./ui/alert-dialog";
export * from "./ui/table";
export * from "./ui/tabs";
export * from "./ui/badge";
export * from "./ui/chip";
export * from "./ui/card";
export * from "./ui/separator";
export * from "./ui/popover";
export * from "./ui/tooltip";
export * from "./ui/scroll-area";
export * from "./ui/toolbar";
