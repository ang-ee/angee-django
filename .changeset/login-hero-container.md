---
"@angee/app": minor
"@angee/ui": minor
---

A theme or branding fragment can own the sign-in presentation by contributing
one child to the new `auth.login#hero` container. Its content is a
`LoginHeroContent` (`@angee/ui/runtime`): a `render` component, an optional
`presentation: "full-bleed"` and an optional `backgroundImageUrl`. `LoginPage`
renders it on every page that composes it unless an explicit `hero` prop is
passed; a second contributor, or a child without a `render` component, fails at
boot. A host's hero (the prop or a contribution) now renders at every width,
stacked above the sign-in card on small screens; the framework's own hero still
shows from `lg` up. `LoginPage` also takes `presentation="full-bleed"`, and its
`presentation` and `backgroundImageUrl` serve only its own hero: a contributed
hero brings its own. The sign-in URL is the declared path of the `auth.login`
route, which the auth gates, the auth provider and `useLoginPath` read.
Upgrade: remove `loginPath` from `createApp`'s input and declare the
`auth.login` route at that path instead.
