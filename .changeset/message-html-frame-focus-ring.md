---
"@angee/ui": minor
---

**Changed API.** `@angee/ui/lib/use-motion-tokens` is removed and
`@angee/ui/lib/use-root-tokens` is added: `useMotionTokens` lives there now,
beside `useRootTokens`, the one reader of the root's resolved tokens. Import
`useMotionTokens` from `@angee/ui/lib/use-root-tokens`.

`MessageHtmlFrame` shows when the keyboard is in a mail. The framed body is a
tab stop of its own, whether or not the mail scrolls, so a mail with links gains
one stop, the mail itself, before its first link. While the body or anything in
the mail has keyboard focus, a ring stays on the frame's edges: the theme's
`--border-focus` outside, and inside it the white or black that contrasts with
it, so one edge holds 3:1 on a white mail and on a near-black one; on a mid-grey
mail neither edge may. A click on the mail's text or on a link shows no ring
until the next key press; a click into a field of the mail shows it, as the
field shows its own. The
framed document carries the frame's title. The ring's color is part of the
framed document, so a theme, brand or light/dark switch reloads an open mail,
which returns to its top and shows no ring until the next Tab.

The ring needs `:has()` (Chrome 105, Safari 15.4, Firefox 121); a browser
without it draws none, as before. Where CSS relative color syntax is missing,
the theme's color is drawn as it is, with a black and a white line inside it.
