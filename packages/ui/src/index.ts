// @angee/ui — the single rendered binding. The styling foundation (the one
// class-merge config + `cn`, the `tv` recipe factory, the tone/fill vocabulary),
// the drag-and-drop seam, and the slot/render helpers live here; everything that
// renders builds on them. (Wave A.0: `lib/` relocated from `@angee/base`; the
// rendered primitives/views/chrome follow in later waves.)

export * from "./lib";

// Render leaves the primitives/chrome build on: the glyph renderer + icon
// registry (and the brand/agent marks they resolve) and the base-namespace
// translator. (Wave A.0b: relocated from `@angee/base`.)
export * from "./chrome/Glyph";
export * from "./chrome/icon-registry";
export * from "./chrome/AgentGlyph";
export * from "./chrome/AngeeMark";
export * from "./i18n";

// UI primitives — embeddable, SDK-agnostic atoms on base-ui. (Wave A.1:
// relocated from `@angee/base`.)
export * from "./ui/button";
export * from "./ui/spinner";
export * from "./ui/skeleton";
export * from "./ui/field";
export * from "./ui/form";
export * from "./ui/inline-text-action";
export * from "./ui/label";
export * from "./ui/input";
export * from "./ui/textarea";
export * from "./ui/select";
export * from "./ui/pager";
export * from "./ui/checkbox";
export * from "./ui/switch";
export * from "./ui/slider";
export * from "./ui/number-field";
export * from "./ui/radio-group";
export * from "./ui/calendar";
export * from "./ui/dialog";
export * from "./ui/alert-dialog";
export * from "./ui/table";
export * from "./ui/tabs";
export * from "./ui/accordion";
export * from "./ui/collapsible";
export * from "./ui/drawer";
export * from "./ui/toggle";
export * from "./ui/toggle-group";
export * from "./ui/badge";
export * from "./ui/chip";
export * from "./ui/avatar";
export * from "./ui/status-icon";
export * from "./ui/kbd";
export * from "./ui/code";
export * from "./ui/alert";
export * from "./ui/text-link";
export * from "./ui/nav-link";
export * from "./ui/section-eyebrow";
export * from "./ui/card";
export * from "./ui/separator";
export * from "./ui/popover";
export * from "./ui/dropdown-menu";
export * from "./ui/context-menu";
export * from "./ui/navigation-menu";
export * from "./ui/command";
export * from "./ui/selection-bar";
export * from "./ui/tooltip";
export * from "./ui/scroll-area";
export * from "./ui/toolbar";
export * from "./ui/form-layout";
export * from "./ui/upload-drop-target";

// Widgets — the field renderers + the default widget registry — and the
// feedback surface (modals, toasts, the Refine notification bridge). (Wave A.2:
// relocated from `@angee/base`.)
export * from "./widgets";
export * from "./feedback";
