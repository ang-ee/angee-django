import { tv, type VariantProps } from "../lib/variants";

// The small set of named text roles shared across panels, lists, and headers.
// `display` is the 22px page/record display title; `title` is the 15px inline
// panel/list title; `heading` is the 18px section/detail heading. `value` is the
// 13px tabular numeric read/cell value. `caption`/`meta` keep their muted
// secondary sizes, while `description` is non-muted 13px secondary body text
// (text-fg-2) for dialog/drawer/accordion bodies. `tone` lets a title-like role
// keep its typography while switching foreground: a two-tone heading is its
// `display`/`heading` text followed by
// `<span className={textRoleVariants({ role: "display", tone: "muted" })}>`.
// `numeric`/`mono` add tabular figures or monospaced text, and `truncate` adds
// the shared overflow treatment. Call sites compose this recipe instead of
// re-spelling size/weight/color literals.
export const textRoleVariants = tv({
  variants: {
    role: {
      display: "text-22 font-semibold text-fg",
      title: "text-15 font-semibold text-fg",
      heading: "text-lg font-semibold text-fg",
      value: "text-13 tabular-nums text-fg",
      caption: "text-2xs text-fg-muted",
      meta: "text-13 text-fg-muted",
      description: "text-13 text-fg-2",
    },
    tone: {
      fg: "",
      muted: "text-fg-muted",
    },
    numeric: {
      true: "tabular-nums",
      false: "",
    },
    mono: {
      true: "font-mono",
      false: "",
    },
    truncate: {
      true: "truncate",
      false: "",
    },
  },
  defaultVariants: {
    tone: "fg",
  },
});

export type TextRoleRecipeProps = VariantProps<typeof textRoleVariants>;
export type TextRole = NonNullable<TextRoleRecipeProps["role"]>;
