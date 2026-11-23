---
"@angee/app": minor
---

A deployment declares its static page head in `ANGEE_UI.shell.site` (`title`,
`description`, `icon`, `touchIcon`, `image`; the type is `ShellSettings.site`),
whose files name assets an addon declares in its `web/package.json`
`angee.assets`. `defineAngeeWebViteConfig` emits those files and writes the
title, description, Open Graph, `twitter:card` and icon tags; absolute preview
URLs come from the `ANGEE_PUBLIC_ORIGIN` environment variable, which must be a
bare http(s) origin or the build and the dev server fail. A deployment with
`shell.brand` and no `shell.site` now gets the brand's name as its `<title>`,
and `generateWebRuntime` returns the resolved head as `head` (`SiteHead`, with
`shellSiteHead`, from `@angee/app/shell-runtime`). Upgrade: a host that declares
`shell.site` removes from its `index.html` every Open Graph and `twitter:card`
tag, and the description and icon tags the site writes, since the build and the
dev server refuse a second owner of one.
