---
"@angee/ui": minor
---

A customised theme's link, and each tone's text (`--brand-soft-text`,
`--accent-soft-text` and the four status texts), now read at 4.5:1 on their own
fills and on every surface of their scheme (canvas, sheets, popover and inset),
in light and dark, wherever any colour can; where the surfaces pull opposite
ways and none can, the text is the colour that comes nearest. The link was
worked out against plain white and the dark ink, so it could fall under that on
a customisation's sheets, most in dark. Texts move toward the ink in hundredths,
so they keep more of their colour. A surface the customisation leaves at its
default counts as the theme's definition states it, else as its defaults derive
it, with a 1% margin: `ThemeOptionsDefinition.resolve` gains an optional second
argument, the definition's own token layers, which `resolveThemeOptions` passes.
A text is worked out only when its own input changes, so changing only surfaces
keeps the theme's own link and tone texts. A theme's own colours are unchanged.
The theme runtime feeds the theme catalogue's fingerprint, so each device drops
its first-paint cache once, on its first visit after the upgrade.
