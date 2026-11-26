---
"@angee/ui": patch
---

A customised theme's link, and each tone's text (`--brand-soft-text`,
`--accent-soft-text` and the four status texts), now read at 4.5:1 on every
surface of their scheme (canvas, sheets, popover and inset), in light and dark,
besides the tone's own fills. The link was worked out against plain white and
the dark ink, so it could fall under that on a customisation's sheets, most in
dark. A surface the customisation leaves at its default counts as the theme's
defaults derive it. A theme's own colours are unchanged. The theme runtime
feeds the theme catalogue's fingerprint, so each device drops its first-paint
cache once, on its first visit after the upgrade.
