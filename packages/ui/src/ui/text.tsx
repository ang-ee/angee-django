import { tv, type VariantProps } from "../lib/variants";

// The small set of named text roles shared across panels, lists, and headers.
// `title` is the inline panel/list/page title (15px, the dominant spelling);
// `heading` is the larger detail-page H1 (18px) kept as a DISTINCT role (its own
// intent). `caption`/`meta` name the two muted secondary sizes; `description` is
// non-muted 13px secondary body text (text-fg-2) for dialog/drawer/accordion
// bodies. Call sites compose this recipe instead of re-spelling the
// size/weight/color literals.
export const textRoleVariants = tv({
  variants: {
    role: {
      title: "text-15 font-semibold text-fg",
      heading: "text-lg font-semibold text-fg",
      caption: "text-2xs text-fg-muted",
      meta: "text-13 text-fg-muted",
      description: "text-13 text-fg-2",
    },
    truncate: {
      true: "truncate",
      false: "",
    },
  },
});

export type TextRoleRecipeProps = VariantProps<typeof textRoleVariants>;
export type TextRole = NonNullable<TextRoleRecipeProps["role"]>;
