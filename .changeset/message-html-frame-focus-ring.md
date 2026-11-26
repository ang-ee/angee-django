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
the mail has keyboard focus, a ring stays on the frame's edges, in three tones
of 2px each: the theme's `--border-focus` outside, then a black line, then a
white one, so one of them holds contrast on any mail, whatever it paints and
whatever the theme's color is (at least 4.6:1 on any grey). A click on the
mail's text or on a link shows no ring
until the next key press; a click into a field of the mail shows it, as the
field shows its own. The
framed document carries the frame's title. The ring's color is part of the
framed document, so a theme, brand or light/dark switch reloads an open mail,
which returns to its top and shows no ring until the next Tab.

The ring needs `:has()` (Chrome 105, Safari 15.4, Firefox 121) and nothing
newer; a browser without it draws none, as before.
